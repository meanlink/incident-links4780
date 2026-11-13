"""服务事件窗口Jaccard和有向时滞关联。

支持两种后继配对口径：
- shared（默认，原始口径）：每个源事件独立匹配窗口内最早且严格晚于它的后继事件，
  不同源事件允许共用同一个后继，结果与历史分析完全一致。
- exclusive（不复用后继的复盘口径）：同一服务对内每个事件最多参与一次配对，
  先最大化有效配对数，再在所有最大配对中最小化总时滞；仍要求后继严格晚于源事件且不超出时窗。
"""
from collections import defaultdict
import numpy as np
from scipy.optimize import linear_sum_assignment

SHARED = "shared"
EXCLUSIVE = "exclusive"
MATCH_MODES = (SHARED, EXCLUSIVE)
# 配对被拒绝/未匹配的原因码，结果页据此回查
REASON_NO_CANDIDATE = "no_candidate"
REASON_BEYOND_WINDOW = "beyond_window"
REASON_NOT_EARLIER = "not_earlier"
REASON_TAKEN = "exclusive_taken"
REASON_NO_PRIOR_SOURCE = "no_prior_source"
REASON_SOURCE_TAKEN = "exclusive_source_taken"


def metadata():
    return {
        "name": "服务故障共现研判", "short": "incident-links",
        "tagline": "把同窗共现和先后跟随分开量化，定位值得复查的传播链。",
        "client": "桥石数服 · 7人可靠性小组 · 5个服务的告警复核事件",
        "workflow": "值班工程师翻阅多份告警时间线，手工判断缓存、网关与队列异常是否同时出现。",
        "pains": ["告警次数不同使原始共同次数不可比", "同窗共现容易被误读成先后传播", "人工时间线无法复现实验窗口变化"],
        "upstream": "已去除重复通知的事件归档 service_events_week45.csv；时间以周一00:00起累计分钟表示",
        "downstream": "可靠性复盘会对高Jaccard与短时滞的服务对核查调用日志",
        "entities": ["服务复核事件", "时窗关联试验", "可靠性复盘简报"],
        "dataset_name": "桥石五服务本周复核事件序列",
        "columns": [{"key": "event_id", "label": "复核事件ID", "type": "string"}, {"key": "service", "label": "服务名称", "type": "string"}, {"key": "occurred_min", "label": "周内累计分钟", "type": "number"}, {"key": "severity", "label": "事件等级(1~3)", "type": "number"}],
        "parameters": [{"key": "window_min", "label": "同窗及跟随窗口(分钟)", "type": "number", "default": 5, "min": 1, "max": 30}, {"key": "min_pairs", "label": "最少跟随事件数", "type": "number", "default": 3, "min": 2, "max": 20}, {"key": "match_mode", "label": "后继配对口径", "type": "select", "default": SHARED, "options": [{"value": SHARED, "label": "复用后继（原口径，不同源事件可共享同一后继）"}, {"value": EXCLUSIVE, "label": "不复用后继（一对一复盘，先配对最多再总时滞最小）"}]}],
        "workbench_title": "服务事件时窗复盘", "action_label": "计算共现与有向时滞", "unit": "个复核事件",
        "accent": "oklch(63% 0.12 155)", "style": "control-room-industrial",
        "seed_story": ["09:00 值班工程师导入 service_events_week45.csv", "09:10 确认事件已按event_id去重，时间原点均为周一00:00", "09:20 选择5分钟窗口、至少3次跟随", "09:21 后端分别计算占用时间窗Jaccard与最近后继时滞", "09:30 工程师比较cache→api与api→cache方向的跟随率", "09:40 将窗口改为10分钟，查看链条扩张", "10:00 下载服务对明细，回查原始调用链", "17:00 管理员安排每周一09:00可靠性复盘简报"],
        "method": _method_text(SHARED),
        "limitations": ["窗口边界会影响Jaccard，必须比较多个窗口", "多个A可共享后继B，不表示一对一故障传播", "共现或先后关系均不足以证明因果"],
    }


def _method_text(mode):
    if mode == EXCLUSIVE:
        return ("固定窗口以累计分钟0为原点；Jaccard=共同占用窗/任一服务占用窗。不复用后继口径下，同一服务对内每个事件最多参与一次配对："
                "仅允许严格晚于源事件且时滞不超过窗口的候选，先让有效配对数最多，再在全部最大配对中让总时滞最小（指派优化，结果对输入顺序不敏感）；"
                "跟随率=配对成功的源事件数/源事件总数，输出配对时滞的中位数与总时滞，并保留每个未匹配事件的原因供复盘回查。")
    return "固定窗口以累计分钟0为原点；Jaccard=共同占用窗/任一服务占用窗。对每个A事件，仅匹配窗口内最早且严格晚于它的B事件；跟随率=匹配A事件数/A总事件数，输出中位时滞。每个A独立匹配，允许不同A共享B。"


def seed_records():
    rng = np.random.default_rng(1013)
    events = []
    def add(service, minute, severity):
        events.append({"event_id": f"QS-{len(events)+1:04}", "service": service, "occurred_min": round(float(minute), 2), "severity": int(severity)})
    for i in range(55):
        base = i * 60 + float(rng.uniform(4, 15))
        add("cache", base, 2)
        add("api-gateway", base + float(rng.uniform(1.5, 4.5)), 2)
        if i % 3 == 0:
            add("job-queue", base + float(rng.uniform(6, 11)), 1)
        if i % 4 == 0:
            add("metrics-db", base - float(rng.uniform(2, 4)), 3)
    for _ in range(45):
        add(str(rng.choice(["cache", "api-gateway", "job-queue", "metrics-db", "object-store"])), rng.uniform(0, 3300), rng.integers(1, 4))
    return sorted(events, key=lambda row: row["occurred_min"])


def _parse(records):
    """校验并按服务归集为按(时间,事件ID)排序的事件列表，保证乱序输入结果一致。"""
    if len(records) < 30:
        raise ValueError("至少需要30个已去重事件。")
    grouped = defaultdict(list)
    identifiers = set()
    for row in records:
        try:
            service = str(row["service"]).strip()
            minute = float(row["occurred_min"])
            severity = float(row["severity"])
            event_id = str(row["event_id"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("事件ID、服务、累计分钟与等级须完整。") from exc
        if not service or not event_id or not np.isfinite(minute) or minute < 0 or severity not in (1, 2, 3):
            raise ValueError("事件ID与服务不能为空，时间须非负有限，等级须1~3。")
        if event_id in identifiers:
            raise ValueError("事件ID重复，请先去重，避免人为放大关联。")
        identifiers.add(event_id)
        grouped[service].append({"event_id": event_id, "minute": minute})
    if not 2 <= len(grouped) <= 12:
        raise ValueError("一次试验须包含2~12个服务。")
    ordered = {
        key: sorted(items, key=lambda item: (item["minute"], item["event_id"]))
        for key, items in grouped.items()
    }
    return ordered


def _shared_lags(source_events, target_events, window):
    """原口径：每个源事件独立取窗口内最早且严格晚于它的后继，允许后继被复用。"""
    target_times = np.array([item["minute"] for item in target_events], dtype=float)
    lags = []
    for item in source_events:
        instant = item["minute"]
        index = int(np.searchsorted(target_times, instant, side="right"))
        if index < len(target_times) and target_times[index] - instant <= window:
            lags.append(float(target_times[index] - instant))
    return lags


def _exclusive_assignment(source_events, target_events, window):
    """一对一指派：先最大化有效配对数，再最小化总时滞。

    采用矩形指派（线性和）：无效候选给统一大代价，优化器因此优先选择有效边（配对最多），
    再在有效边中按时滞最小化；同代价边再叠加按事件次序的确定性微小罚分，
    使乱序输入也得到完全相同的配对结果。
    返回 (配对列表, 未匹配源原因, 未匹配目标原因)。
    """
    n, m = len(source_events), len(target_events)
    size = max(n, m, 1)
    big = 1e9
    cost = [[big] * size for _ in range(size)]
    # 记录每个矩阵格子对应的时滞（无效边为 None）
    lag_matrix = [[None] * size for _ in range(size)]
    for i, src in enumerate(source_events):
        for j, dst in enumerate(target_events):
            lag = dst["minute"] - src["minute"]
            if 0 < lag <= window:
                # 次序罚分远小于任意有效时滞差异，不改变“先配对最多、再总时滞最小”的口径
                cost[i][j] = lag + 1e-9 * (i * size + j)
                lag_matrix[i][j] = float(lag)
    row_ind, col_ind = linear_sum_assignment(np.array(cost, dtype=float))
    matched_pairs = []
    matched_source = set()
    matched_target = set()
    for i, j in zip(row_ind, col_ind):
        if i < n and j < m and lag_matrix[i][j] is not None:
            matched_pairs.append({
                "source_event_id": source_events[i]["event_id"],
                "source_minute": round(source_events[i]["minute"], 3),
                "target_event_id": target_events[j]["event_id"],
                "target_minute": round(target_events[j]["minute"], 3),
                "lag_min": round(lag_matrix[i][j], 3),
            })
            matched_source.add(i)
            matched_target.add(j)
    matched_pairs.sort(key=lambda pair: (pair["source_minute"], pair["source_event_id"]))

    unmatched_sources = []
    for i, src in enumerate(source_events):
        if i in matched_source:
            continue
        instant = src["minute"]
        later = [dst for dst in target_events if dst["minute"] > instant]
        if not later:
            reason = REASON_NO_CANDIDATE
            detail = "窗口内没有严格晚于该事件的后继事件"
        elif later[0]["minute"] - instant > window:
            reason = REASON_BEYOND_WINDOW
            detail = f"最近后继晚{round(later[0]['minute'] - instant, 3)}分钟，超出{window:g}分钟窗口"
        else:
            reason = REASON_TAKEN
            detail = "窗口内后继已被其他源事件配对（一对一口径下后继不可复用）"
        unmatched_sources.append({"event_id": src["event_id"], "minute": round(instant, 3), "reason": reason, "reason_detail": detail})

    unmatched_targets = []
    for j, dst in enumerate(target_events):
        if j in matched_target:
            continue
        instant = dst["minute"]
        earlier = [src for src in source_events if src["minute"] < instant]
        if not earlier:
            reason = REASON_NO_PRIOR_SOURCE
            detail = "没有严格早于该事件的源事件"
        elif instant - earlier[-1]["minute"] > window:
            reason = REASON_NOT_EARLIER
            detail = "最近源事件超出跟随窗口，无法形成有效时滞"
        else:
            reason = REASON_SOURCE_TAKEN
            detail = "窗口内源事件已与其他后继配对（一对一口径下源事件不可复用）"
        unmatched_targets.append({"event_id": dst["event_id"], "minute": round(instant, 3), "reason": reason, "reason_detail": detail})
    return matched_pairs, unmatched_sources, unmatched_targets


def analyze(records, params):
    try:
        window = float(params.get("window_min", 5))
        raw_min = float(params.get("min_pairs", 3))
        minimum = int(raw_min)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError("窗口与最少跟随次数须为有效数值。") from exc
    mode = str(params.get("match_mode", SHARED)).strip() or SHARED
    if mode not in MATCH_MODES:
        raise ValueError("配对口径须为复用后继或不复用后继之一。")
    if not 1 <= window <= 30 or raw_min != minimum or not 2 <= minimum <= 20:
        raise ValueError("窗口须1~30分钟，最少跟随数须2~20的整数。")
    events_by_service = _parse(records)
    times = {key: np.array([item["minute"] for item in items], dtype=float) for key, items in events_by_service.items()}
    occupied = {key: {int(np.floor(value / window)) for value in values} for key, values in times.items()}
    exclusive = mode == EXCLUSIVE
    rows = []
    pair_review = []
    for a in sorted(times):
        for b in sorted(times):
            if a == b:
                continue
            source_events = events_by_service[a]
            target_events = events_by_service[b]
            union = len(occupied[a] | occupied[b])
            intersection = len(occupied[a] & occupied[b])
            base = {
                "source": a, "target": b, "source_events": len(times[a]),
                "jaccard": round(intersection / union, 4), "shared_windows": intersection,
            }
            if not exclusive:
                lags = _shared_lags(source_events, target_events, window)
                if len(lags) < minimum:
                    continue
                rows.append({**base, "follow_count": len(lags),
                             "follow_pct": round(len(lags) / len(times[a]) * 100, 2),
                             "median_lag_min": round(float(np.median(lags)), 3)})
                continue

            pairs, unmatched_sources, unmatched_targets = _exclusive_assignment(source_events, target_events, window)
            if len(pairs) < minimum:
                continue
            lags = [pair["lag_min"] for pair in pairs]
            link_seed = f"{a}→{b}"
            rows.append({**base, "follow_count": len(pairs),
                         "follow_pct": round(len(pairs) / len(times[a]) * 100, 2),
                         "median_lag_min": round(float(np.median(lags)), 3),
                         "total_lag_min": round(float(sum(lags)), 3),
                         "unmatched_source": len(unmatched_sources),
                         "unmatched_target": len(unmatched_targets)})
            pair_review.append({"source": a, "target": b, "label": link_seed,
                                "matched_pairs": pairs,
                                "unmatched_sources": unmatched_sources,
                                "unmatched_targets": unmatched_targets})
    if exclusive:
        rows.sort(key=lambda row: (-row["follow_count"], row["total_lag_min"], -row["follow_pct"], row["source"], row["target"]))
        order = {(row["source"], row["target"]): i for i, row in enumerate(rows)}
        pair_review.sort(key=lambda item: order[(item["source"], item["target"])])
    else:
        rows.sort(key=lambda row: (-row["follow_pct"], -row["follow_count"], row["source"], row["target"]))
    for rank, row in enumerate(rows, 1):
        row["link_id"] = f"L{rank:02}"
    for rank, item in enumerate(pair_review, 1):
        item["link_id"] = f"L{rank:02}"
    top = rows[:8]

    if exclusive:
        columns = [{"key": "link_id", "label": "服务对编号"}, {"key": "source", "label": "先发生服务A"}, {"key": "target", "label": "后继服务B"}, {"key": "source_events", "label": "A事件数"}, {"key": "follow_count", "label": "一对一配对数"}, {"key": "follow_pct", "label": "配对覆盖率(%)"}, {"key": "median_lag_min", "label": "配对时滞中位数(min)"}, {"key": "total_lag_min", "label": "配对总时滞(min)"}, {"key": "unmatched_source", "label": "未匹配A事件"}, {"key": "unmatched_target", "label": "未匹配B事件"}, {"key": "jaccard", "label": "同窗Jaccard"}, {"key": "shared_windows", "label": "共同占用窗"}]
        chart_series_name = "配对覆盖率%（服务对编号见明细）"
        summary = f"{len(records)}个事件覆盖{len(times)}个服务；{window:g}分钟窗口、不复用后继口径下，{len(rows)}条有向服务对满足至少{minimum}次一对一配对。"
        findings = (([f"最强一对一配对方向为{rows[0]['source']}→{rows[0]['target']}，{rows[0]['follow_count']}对事件配对成功，总时滞{rows[0]['total_lag_min']}分钟、中位时滞{rows[0]['median_lag_min']}分钟。"] if rows else ["当前窗口与最少次数下没有候选服务对，可核对时间范围后调整窗口。"])
                    + ["该口径先保证有效配对最多，再在全部最大配对中让总时滞最小；每个事件在同一服务对内最多配对一次。",
                       "配对仍要求后继严格晚于源事件且不超出窗口；未匹配事件及原因可在下方配对回查中逐条核对。"])
        warnings = ["有向先后关系不是故障因果链，需回查调用与变更记录。"] + (["没有满足阈值的服务对。"] if not rows else [])
        result = {
            "summary": summary,
            "metrics": [{"label": "复核事件", "value": len(records)}, {"label": "服务数量", "value": len(times)}, {"label": "有向关联候选", "value": len(rows)}, {"label": "最多一对一配对", "value": max([row["follow_count"] for row in rows], default=0)}, {"label": "最高配对覆盖率", "value": max([row["follow_pct"] for row in rows], default=0), "unit": "%"}],
            "table": {"columns": columns, "rows": rows},
            "chart": {"type": "bar", "labels": [row["link_id"] for row in top], "series": ([{"name": chart_series_name, "values": [row["follow_pct"] for row in top]}] if top else [])},
            "findings": findings,
            "method": _method_text(EXCLUSIVE), "warnings": warnings,
            "match_mode": EXCLUSIVE, "pair_review": pair_review,
        }
        return result

    columns = [{"key": "link_id", "label": "服务对编号"}, {"key": "source", "label": "先发生服务A"}, {"key": "target", "label": "后继服务B"}, {"key": "source_events", "label": "A事件数"}, {"key": "follow_count", "label": "跟随次数"}, {"key": "follow_pct", "label": "跟随率(%)"}, {"key": "median_lag_min", "label": "中位时滞(min)"}, {"key": "jaccard", "label": "同窗Jaccard"}, {"key": "shared_windows", "label": "共同占用窗"}]
    return {
        "summary": f"{len(records)}个事件覆盖{len(times)}个服务；{window:g}分钟窗口下，{len(rows)}条有向服务对满足至少{minimum}次跟随。",
        "metrics": [{"label": "复核事件", "value": len(records)}, {"label": "服务数量", "value": len(times)}, {"label": "有向关联候选", "value": len(rows)}, {"label": "最高跟随率", "value": max([row["follow_pct"] for row in rows], default=0), "unit": "%"}],
        "table": {"columns": columns, "rows": rows},
        "chart": {"type": "bar", "labels": [row["link_id"] for row in top], "series": ([{"name": "跟随率%（服务对编号见明细）", "values": [row["follow_pct"] for row in top]}] if top else [])},
        "findings": (([f"最强跟随方向为{rows[0]['source']}→{rows[0]['target']}，中位时滞{rows[0]['median_lag_min']}分钟。"] if rows else ["当前窗口与最少次数下没有候选服务对，可核对时间范围后调整窗口。"])+["Jaccard衡量同窗重合，有向跟随率衡量先后顺序，两者不可互相替代。", "每个源事件仅取最近后继；扩大窗口可能提高跟随率，也会引入偶然匹配。"]),
        "method": _method_text(SHARED), "warnings": ["有向先后关系不是故障因果链，需回查调用与变更记录。"] + (["没有满足阈值的服务对。"] if not rows else []),
        "match_mode": SHARED,
    }
