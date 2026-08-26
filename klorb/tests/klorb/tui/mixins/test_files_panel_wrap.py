# © Copyright 2026 Aaron Kimball
"""Layout regression test for klorb.tui.widgets.files_panel.FilesPanel row wrapping."""

from collections.abc import Callable
from unittest.mock import MagicMock

from textual.widgets import OptionList
from tui.conftest import _session

from klorb.session import SessionConfig
from klorb.tui.app import ReplApp
from klorb.tui.constants import FILES_PANEL_ID
from klorb.tui.widgets.files_panel import FILES_LIST_ID, FileActivityRowData, FilesPanel


async def test_long_path_rows_never_wrap_even_with_a_scrollbar(
    make_session_config: Callable[..., SessionConfig]
) -> None:
    app = ReplApp(session=_session(MagicMock(), make_session_config))

    async with app.run_test(size=(120, 40)) as pilot:
        panel = app.query_one(f"#{FILES_PANEL_ID}", FilesPanel)
        panel.display = True
        option_list = app.query_one(f"#{FILES_LIST_ID}", OptionList)

        rows = [
            FileActivityRowData(
                abs_path=f"/ws/dir{i}/nested/deep/file.txt",
                rel_path=f"some/very/deeply/nested/directory{i}/structure/file.txt",
                mode="read",
            )
            for i in range(60)
        ]
        panel.show_rows(rows)
        await pilot.pause()

        assert option_list.show_vertical_scrollbar is True
        heights = option_list._heights
        assert all(heights[index] == 1 for index in range(len(rows)))
