"""聊天室回复路径的 NameError 守护。

`_reply_connor` 里曾写着 `room.get("type")`，但这个函数根本没有 `room` 变量
（`room` 是别的函数的参数），于是每次调用都会抛
`NameError: name 'room' is not defined`，让 Gemini 在 1v1 / 群聊里回复失败。

这类错误很隐蔽：它不在任何 try 里（只有外层兜底），从代码上完全看不出问题。
所以用静态检查把它钉住——只要再出现「函数体内用了未定义的 room」就红。
"""

import ast
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent
CHATROOM_PY = ROOT / "routes" / "chatroom.py"


def _module_tree():
    return ast.parse(CHATROOM_PY.read_text(encoding="utf-8"))


def _free_names(node, enclosing_free=frozenset()):
    """返回函数体内被读取、但既不是参数也不是本地赋值的名字。

    ``enclosing_free`` 是外层函数遗留的自由变量集合（闭包捕获）。
    """
    args = {a.arg for a in node.args.args + node.args.kwonlyargs}
    if node.args.vararg:
        args.add(node.args.vararg.arg)
    if node.args.kwarg:
        args.add(node.args.kwarg.arg)
    assigned = set()
    loaded = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Name):
            if isinstance(child.ctx, ast.Store):
                assigned.add(child.id)
            elif isinstance(child.ctx, ast.Load):
                loaded.add(child.id)
    return loaded - args - assigned - set(enclosing_free)


def _enclosing_free_names(tree):
    """算出每个函数可以从外层闭包捕获的自由变量。

    Python 的闭包语义：内层函数里没定义的名字，会去外层函数的作用域找。
    所以只要外层函数里有 ``room = ...``，内层用 ``room`` 就是合法的。
    """
    scopes = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        assigned = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                assigned.add(child.id)
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child is not node:
                assigned.add(child.name)
        scopes.append((node, assigned))
    return scopes


class UndefinedRoomUsageTests(unittest.TestCase):
    """`_reply_connor` 之类的函数不得使用未定义的 room。"""

    def _functions_using_room(self):
        """找出使用了「真正自由」的 room 的函数。

        逐层下钻：外层函数里有 ``room = ...`` 时，内层函数的 room 是闭包捕获，
        合法（如 ``_bg_generate`` 捕获 send_message 的 room）。
        """
        tree = _module_tree()
        offenders = []

        def walk(node, inherited):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return
            free = _free_names(node, inherited)
            if "room" in free:
                offenders.append(node.name)
            assigned = set()
            for child in ast.walk(node):
                if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                    assigned.add(child.id)
            # 内层函数能捕获外层的 room
            inherited_for_children = inherited | (assigned & {"room"})
            for child in node.body:
                walk(child, inherited_for_children)

        for node in tree.body:
            walk(node, frozenset())
        return offenders

    def test_no_function_uses_an_undefined_room(self):
        offenders = self._functions_using_room()
        self.assertEqual(
            [], offenders,
            "以下函数使用了未定义的 room，会在运行时抛 NameError："
            f"{offenders}",
        )

    def test_reply_connor_resolves_room_type_internally(self):
        """`_reply_connor` 必须自己查房间类型，而不是引用不存在的 room 变量。"""
        source = CHATROOM_PY.read_text(encoding="utf-8")
        tree = ast.parse(source)
        target = next(
            node for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_reply_connor"
        )
        body = ast.get_source_segment(source, target)
        self.assertIn("_get_room_type(room_id)", body)
        self.assertNotIn('room.get("type")', body)


class RoomTypeHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_room_type_reads_the_database(self):
        import aiosqlite

        from routes import chatroom as chatroom_routes

        db = await aiosqlite.connect(":memory:")
        db.row_factory = aiosqlite.Row
        await db.execute(
            "CREATE TABLE chatroom_rooms (id TEXT PRIMARY KEY, type TEXT)"
        )
        await db.execute("INSERT INTO chatroom_rooms VALUES ('r1','connor_1v1')")
        await db.commit()

        class _Ctx:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *exc):
                return False

        with patch.object(chatroom_routes, "get_db", lambda: _Ctx()):
            self.assertEqual(
                "connor_1v1", await chatroom_routes._get_room_type("r1")
            )
            self.assertEqual(
                "", await chatroom_routes._get_room_type("missing")
            )
        await db.close()


if __name__ == "__main__":
    unittest.main()
