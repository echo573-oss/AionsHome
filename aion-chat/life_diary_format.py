"""一日一夜日记 Markdown 的解析与最小侵入合并。

只负责纯文本处理：读出 frontmatter 与 Callout 区块，写回时只改动自己负责的字段，
其余内容（Calendar、每日图片、手写正文、未知 frontmatter 键）原样保留。
"""
from __future__ import annotations

import re
from datetime import datetime

# 允许写入的 frontmatter 字段与取值形态。APP 自动计算的字段一律不在表内。
ALLOWED_FIELDS: dict[str, str] = {
    'mood': 'int',
    'energy': 'int',
    'anxiety': 'int',
    'sleep_start': 'text',
    'sleep_end': 'text',
    'sleep_quality': 'int',
    'exercise_type': 'list',
    'exercise_class': 'text',
    'exercise_location': 'list',
    'exercise_note': 'text',
    'diet_note': 'text',
    'eat_out_note': 'text',
    'mode': 'list',
    'domains': 'list',
    'today_keywords': 'list',
    'weather': 'text',
}

# 区块名称 -> (callout 类型, 标题, 条目样式)
BLOCK_TYPES: dict[str, tuple[str, str, str]] = {
    '完成': ('success', '✅ 完成', 'numbered'),
    'lesson': ('tip', '💡 lesson learned', 'text'),
    '外出就餐': ('info', '🍽 外出就餐', 'text'),
    '小开心': ('example', '🎁 小开心', 'text'),
    '元宝记录': ('quote', '👶 元宝记录', 'text'),
    '花费': ('warning', '💰 值得记录的花费', 'bulleted'),
    '读书': ('abstract', '📚 今日读书', 'bulleted'),
    '自由记录': ('note', '🔄 自由记录', 'text'),
    '明日 TODO': ('info', '🌱 明日 TODO', 'numbered'),
}

BLOCK_ORDER = ['完成', 'lesson', '外出就餐', '小开心', '元宝记录', '花费', '读书', '自由记录', '明日 TODO']

CALLOUT_RE = re.compile(r'^> \[!(\w+)\] (.*)$')
KEY_RE = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']


def weekday_label(date: str) -> str:
    try:
        return WEEKDAYS[datetime.strptime(date, '%Y-%m-%d').weekday()]
    except ValueError:
        return ''


# ── frontmatter ──────────────────────────────────────────────

def split_frontmatter(lines: list[str]) -> tuple[list[str], list[str]]:
    if not lines or lines[0].strip() != '---':
        return [], lines
    for index in range(1, len(lines)):
        if lines[index].strip() == '---':
            return lines[1:index], lines[index + 1:]
    return [], lines


def parse_frontmatter(frontmatter: list[str]) -> list[dict]:
    entries: list[dict] = []
    for line in frontmatter:
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue
        if line.startswith((' ', '\t', '-')):
            # 上一条目的块式列表续行
            if entries:
                entries[-1]['lines'].append(line)
            continue
        match = KEY_RE.match(line)
        if not match:
            continue
        entries.append({'key': match.group(1), 'raw': match.group(2).strip(), 'lines': [line]})
    for entry in entries:
        entry['value'] = _entry_value(entry)
    return entries


def _entry_value(entry: dict) -> dict:
    raw = entry['raw']
    if raw.startswith('[') and raw.endswith(']'):
        items = [item.strip().strip('"\'') for item in raw[1:-1].split(',')]
        return {'type': 'list', 'items': [item for item in items if item], 'style': 'inline'}
    if raw == '':
        items = []
        for line in entry['lines'][1:]:
            text = line.strip()
            if text.startswith('- '):
                items.append(text[2:].strip())
        if items:
            return {'type': 'list', 'items': items, 'style': 'block'}
        return {'type': 'scalar', 'value': '', 'style': 'scalar'}
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in '"\'':
        raw = raw[1:-1]
    return {'type': 'scalar', 'value': raw, 'style': 'scalar'}


def set_field(entries: list[dict], key: str, value) -> bool:
    """写入或更新一个 frontmatter 字段，返回是否发生变化。"""
    target = next((entry for entry in entries if entry['key'] == key), None)
    if target is None:
        style = 'inline' if isinstance(value, list) and len(value) == 1 else 'block'
        lines = _render_value_lines(key, value, style)
        entry = {'key': key, 'raw': lines[0].split(':', 1)[1].strip(), 'lines': lines}
        entry['value'] = _entry_value(entry)
        index = len(entries)
        for position, existing in enumerate(entries):
            if existing['key'] == 'tags':
                index = position
                break
        entries.insert(index, entry)
        return True
    if target['value']['type'] == 'list' and isinstance(value, list):
        if list(target['value']['items']) == list(value):
            return False
        style = target['value'].get('style') or ('inline' if len(value) == 1 else 'block')
    elif target['value']['type'] == 'scalar' and not isinstance(value, list):
        if str(target['value']['value']) == str(value):
            return False
        style = 'scalar'
    else:
        style = 'inline' if isinstance(value, list) and len(value) == 1 else 'block'
    lines = _render_value_lines(key, value, style)
    target['lines'] = lines
    target['raw'] = lines[0].split(':', 1)[1].strip()
    target['value'] = _entry_value(target)
    return True


def _render_value_lines(key: str, value, style: str) -> list[str]:
    if isinstance(value, list):
        if style == 'inline':
            return [f'{key}: [' + ', '.join(str(item) for item in value) + ']']
        return [f'{key}:'] + [f'  - {item}' for item in value]
    return [f'{key}: {value}']


def render_frontmatter(entries: list[dict]) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        lines.extend(entry['lines'])
    return lines


# ── 正文区块 ────────────────────────────────────────────────

def parse_body(body: list[str]) -> list[list[str]]:
    """按空行切成区块；Callout 内部的 ">" 空行仍属于同一区块。"""
    chunks: list[list[str]] = []
    current: list[str] = []
    for line in body:
        if not line.strip():
            if current:
                chunks.append(current)
                current = []
            continue
        current.append(line)
    if current:
        chunks.append(current)
    return chunks


def parse_callout(chunk: list[str]) -> dict | None:
    if not chunk:
        return None
    match = CALLOUT_RE.match(chunk[0].strip())
    if not match:
        return None
    paragraphs: list[list[str]] = []
    current: list[str] = []
    for line in chunk[1:]:
        text = line[1:] if line.startswith('>') else line
        if text.startswith(' '):
            text = text[1:]
        if not text.strip():
            paragraphs.append(current)
            current = []
        else:
            current.append(text)
    paragraphs.append(current)
    # 头部之后的第一个 ">" 是分隔符，不是段落
    while paragraphs and not paragraphs[0]:
        paragraphs.pop(0)
    while paragraphs and not paragraphs[-1]:
        paragraphs.pop()
    return {'kind': match.group(1), 'title': match.group(2), 'paragraphs': paragraphs}


def render_callout(callout: dict) -> list[str]:
    lines = [f"> [!{callout['kind']}] {callout['title']}"]
    for paragraph in callout['paragraphs']:
        lines.append('>')
        for text in paragraph:
            lines.append(f'> {text}' if text else '>')
    return lines


def _item_lines(callout: dict, style: str) -> list[str]:
    if style == 'numbered':
        pattern = re.compile(r'^\d+[.)]\s')
    elif style == 'bulleted':
        pattern = re.compile(r'^[-*]\s')
    else:
        return []
    return [line for paragraph in callout['paragraphs'] for line in paragraph if pattern.match(line)]


def _has_item(callout: dict, style: str, text: str) -> bool:
    if style == 'text':
        return any(' '.join(paragraph).strip() == text.strip()
                   for paragraph in callout['paragraphs'])
    for line in _item_lines(callout, style):
        body = re.sub(r'^(\d+[.)]|[-*])\s*', '', line).strip()
        if body == text.strip():
            return True
    return False


def _append_item(callout: dict, style: str, text: str) -> None:
    if style == 'numbered':
        number = len(_item_lines(callout, 'numbered')) + 1
        item = f'{number}. {text}'
    elif style == 'bulleted':
        item = f'- {text}'
    else:
        item = None
    if item is None:
        callout['paragraphs'].append(text.split('\n'))
        return
    if callout['paragraphs'] and len(callout['paragraphs']) == 1:
        # 同一条目块内保持紧凑，条目直接续行
        callout['paragraphs'][0].append(item)
        return
    callout['paragraphs'].append([item])


def _callout_header(block_name: str) -> str:
    kind, title, _ = BLOCK_TYPES[block_name]
    return f'> [!{kind}] {title}'


def _insert_chunk(chunks: list[list[str]], block_name: str, chunk: list[str]) -> None:
    """按 APP 的区块顺序插入新 Callout：排在同类区块之前，没有同类就排最前。"""
    order = BLOCK_ORDER.index(block_name) if block_name in BLOCK_ORDER else len(BLOCK_ORDER)
    position = len(chunks)
    for index, existing in enumerate(chunks):
        callout = parse_callout(existing)
        if not callout:
            continue
        name = _block_name_for(callout)
        if name is None:
            continue
        existing_order = BLOCK_ORDER.index(name) if name in BLOCK_ORDER else len(BLOCK_ORDER)
        if existing_order > order:
            position = index
            break
    chunks.insert(position, chunk)


def _block_name_for(callout: dict) -> str | None:
    for name, (kind, title, _) in BLOCK_TYPES.items():
        if callout['kind'] == kind and callout['title'] == title:
            return name
    return None


# ── 对外主流程 ──────────────────────────────────────────────

def _render_document(entries: list[dict], chunks: list[list[str]]) -> str:
    out: list[str] = []
    if entries:
        out.append('---')
        out.extend(render_frontmatter(entries))
        out.append('---')
    for index, chunk in enumerate(chunks):
        if index or entries:
            out.append('')
        out.extend(chunk)
    return '\n'.join(out).strip() + '\n'


def merge_document(existing: str, *, date: str, fields: dict, blocks: list[dict]) -> str:
    """把字段与区块合并进现有日记正文，其余内容原样保留。"""
    lines = existing.splitlines() if existing else []
    frontmatter, body = split_frontmatter(lines)
    entries = parse_frontmatter(frontmatter)
    for key, value in (fields or {}).items():
        set_field(entries, key, value)
    chunks = parse_body(body)
    for block in blocks or []:
        name = block.get('kind')
        text = (block.get('text') or '').strip()
        if name not in BLOCK_TYPES or not text:
            continue
        kind, title, style = BLOCK_TYPES[name]
        header = f'> [!{kind}] {title}'
        target = next((chunk for chunk in chunks if chunk and chunk[0].strip() == header), None)
        if target is None:
            callout = {'kind': kind, 'title': title, 'paragraphs': []}
            _append_item(callout, style, text)
            _insert_chunk(chunks, name, render_callout(callout))
            continue
        callout = parse_callout(target)
        if not callout or _has_item(callout, style, text):
            continue
        _append_item(callout, style, text)
        target[:] = render_callout(callout)
    return _render_document(entries, chunks)


def new_document(date: str, fields: dict, blocks: list[dict]) -> str:
    """新建日记文件：只写 APP 的基础键，其余字段留待 APP 自己补齐。"""
    entries: list[dict] = []
    for key, value in (('date', date), ('weekday', weekday_label(date)),
                       ('type', 'daily'), ('tags', ['一日一页'])):
        set_field(entries, key, value)
    for key, value in (fields or {}).items():
        set_field(entries, key, value)
    chunks: list[list[str]] = []
    for block in blocks or []:
        name = block.get('kind')
        text = (block.get('text') or '').strip()
        if name not in BLOCK_TYPES or not text:
            continue
        kind, title, style = BLOCK_TYPES[name]
        callout = {'kind': kind, 'title': title, 'paragraphs': []}
        _append_item(callout, style, text)
        chunks.append(render_callout(callout))
    out = []
    for chunk in chunks:
        out.append(chunk)
    return _render_document(entries, out)
