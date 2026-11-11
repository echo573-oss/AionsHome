"""生活日记的抽取提示词。

规则与后置哨兵保持同一套底线：只依据本人消息，不把 AI 台词当成本人生活，
不折算分数，不写 APP 自动计算的字段。
"""
from __future__ import annotations

import json
from datetime import datetime

from life_diary_format import ALLOWED_FIELDS, BLOCK_TYPES

FIELDS_HELP = '、'.join(ALLOWED_FIELDS.keys())
BLOCKS_HELP = '、'.join(BLOCK_TYPES.keys())


def extraction_prompt(user_name: str, messages: list[dict], context: list[dict], existing: dict) -> str:
    rules = (
        f'你在整理{user_name}的私人日记。只依据{user_name}本人的消息，'
        'AI 的台词、引用的小说或文档、系统整理的产物都不是本人生活。\n'
        '【值得记的】\n'
        '- 做了什么、去了哪里、见了谁（生活层面的事实）\n'
        '- 身体与心情状态：只有本人明确说出数字时才记分数\n'
        '- 家里在忙的事、进行中的进展\n'
        '- 明确说出口的开心、小确然、意外\n'
        '- 明确说要明天做的事\n'
        '【不记的】\n'
        '- 寒暄、玩笑、对 AI 说的话、指令、问题\n'
        '- 没有事实支撑的情绪宣泄\n'
        '- 密码、证件号、门牌、账号等敏感信息\n'
        '【日期】按事情发生的那一天归档，不是消息发送时间。\n'
        '- 原话里的相对时间（今天/昨天/前天/上周三/30号那天）换算成具体日期。\n'
        '- 没有时间线索就用消息当天的日期。\n'
        '- 补记旧事（“对了，那天我还…”）按上下文指的那天。\n'
        '- 拿不准就用消息当天的日期，并把 date_inferred 写成 true。\n'
        f'【字段】只能从这些里选：{FIELDS_HELP}。\n'
        '- 列表字段写成数组；exercise_type 只写本人说过的运动名。\n'
        '- baby_age_years、baby_age_months、lunar、solar_term、period_*、weight '
        '由日历自动计算，一律不许写。\n'
        '- “心情不错”“有点累”不要折算成分数；只有“心情7分”这种明确数字才写。\n'
        f'【区块】只能从这些里选：{BLOCKS_HELP}。\n'
        '- 完成：明确做完的事\n'
        '- 小开心：明确说出口的开心或小确然\n'
        '- 元宝记录：孩子的成长小事\n'
        '- 明日 TODO：本人明确说要明天做的事\n'
        '- 花费：本人明确说了金额的消费\n'
        '- 读书：本人明确说了今天读了什么\n'
        '- 自由记录：其他有生活价值的片段\n'
        '【去重】已经写过的内容不要重复；同一件事有新进展时更新字段或追加一条，不要重写全文。\n'
        '每条记录都要有 sources（U 标签），至少一个，且必须是这批消息里真实存在的标签。\n'
        '只返回 JSON，没有值得记的就返回 {"days":[]}，一次最多 7 天。\n'
        '格式：{"days":[{"date":"2026-11-08","date_inferred":false,'
        '"fields":{"exercise_type":["网球"]},'
        '"blocks":[{"kind":"自由记录","text":"窗外又有小鸟来了"}],"sources":["U1"]}]}\n'
        f'当前时间：{datetime.now().astimezone().isoformat(timespec="minutes")}。\n'
    )
    sections = [rules]
    if existing:
        sections.append('【已写入的日记】避免重复：\n' + json.dumps(existing, ensure_ascii=False))
    if context:
        sections.append('【最近上下文】只用于判断“那天”指哪一天，不要从中提取新事实：\n'
                        + json.dumps(context, ensure_ascii=False))
    sections.append('【本次消息】\n' + json.dumps(messages, ensure_ascii=False))
    return '\n'.join(sections)
