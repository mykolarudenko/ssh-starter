"""Textual user interface for ssh-starter."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from time import monotonic

from pydantic import ValidationError
from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import DataTable, Header, Input, Markdown, Static

from app.config import AppConfig
from app.history import ConnectionHistory
from app.models import SshConnectionGroup, SshInventory, SshProfile, SshRunResult

GRID_COLUMNS = 3
FIT_HORIZONTAL_RESERVE = 2
DOUBLE_ESCAPE_SECONDS = 1.25
SEARCH_EXTRA_CHARS = {"-", "_", ".", "@"}
USER_PICKER_KEYS = {"f4", "shift+enter", "ctrl+enter", "ctrl+j", "ctrl+m", "ctrl+@"}


class MainProfileGrid(DataTable):
    """Main menu grid with stronger shortcut handling for focused DataTable state."""

    def on_key(self, event: events.Key) -> None:
        app = self.app
        if event.key in USER_PICKER_KEYS and isinstance(app, SshLauncherApp):
            app.action_choose_user()
            event.prevent_default()
            event.stop()
            return
        if event.key == "f1" and isinstance(app, SshLauncherApp):
            app.action_help()
            event.prevent_default()
            event.stop()
            return
        if event.key == "f2" and isinstance(app, SshLauncherApp):
            app.action_options()
            event.prevent_default()
            event.stop()
            return


class ProfileInfoScreen(ModalScreen[None]):
    """Modal profile details screen."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("f10", "quit_app", "Exit"),
    ]

    CSS = """
    ProfileInfoScreen {
        align: center middle;
    }

    #profile-dialog {
        width: 88%;
        height: 82%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #profile-title {
        height: 1;
        text-style: bold;
        color: $accent;
    }

    #profile-help {
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        profile: SshProfile,
        alternatives: tuple[SshProfile, ...],
        warnings: tuple[str, ...],
        history: ConnectionHistory,
    ) -> None:
        super().__init__()
        self.profile = profile
        self.alternatives = alternatives
        self.warnings = warnings
        self.history = history

    def compose(self) -> ComposeResult:
        yield Container(
            Static(f"Profile: {self.profile.alias}", id="profile-title"),
            VerticalScroll(Markdown(self._markdown())),
            Static("Esc close | F10 exit", id="profile-help"),
            id="profile-dialog",
        )

    def action_dismiss(self) -> None:
        self.dismiss()

    def action_quit_app(self) -> None:
        self.app.exit()

    def _markdown(self) -> str:
        identity_files = self.profile.identity_files
        key_lines = "\n".join(f"- `{identity}`" for identity in identity_files)
        if not key_lines:
            key_lines = "- IdentityFile is not set in the matching config; OpenSSH may use agent/default identities."

        proxy_lines: list[str] = []
        if self.profile.proxy_jump:
            proxy_lines.append(f"- ProxyJump: `{self.profile.proxy_jump}`")
        if self.profile.proxy_command:
            proxy_lines.append(f"- ProxyCommand: `{self.profile.proxy_command}`")
        if not proxy_lines:
            proxy_lines.append("- No ProxyJump/ProxyCommand; direct SSH route.")

        alternative_lines = self._alternatives_markdown(self.alternatives)
        warning_lines = self._warnings_markdown(self.warnings)

        return f"""
## Connection

- Alias: `{self.profile.alias}`
- HostName/target: `{self.profile.target_host}`
- User: `{self.profile.display_user}`
- Port: `{self.profile.port or "22"}`
- Source: `{self.profile.source.display()}`
- Last connected: `{self.history.local_display_for(self.profile.alias)}`

## Route

- Connection type: `{"proxied" if self.profile.is_proxied else "direct"}`
- Network: `{self.profile.route_kind.label}`

{chr(10).join(proxy_lines)}

## Key

{key_lines}

## Other profiles for this target

{alternative_lines}

## Parser warnings

{warning_lines}
""".strip()

    @staticmethod
    def _alternatives_markdown(alternatives: tuple[SshProfile, ...]) -> str:
        if not alternatives:
            return "- No other concrete Host aliases target the same HostName and port."
        return "\n".join(
            f"- `{profile.alias}` as `{profile.display_user}` "
            f"via `{profile.route_kind.label}` from `{profile.source.display()}`"
            for profile in alternatives
        )

    @staticmethod
    def _warnings_markdown(warnings: tuple[str, ...]) -> str:
        if not warnings:
            return "- No parser warnings."
        return "\n".join(f"- {warning}" for warning in warnings)


class SessionEndedApp(App[None]):
    """Full-screen message shown after every ssh process exit."""

    TITLE = "ssh-starter"
    SUB_TITLE = "SSH session ended"

    BINDINGS = [
        Binding("space", "return_to_menu", "Menu"),
        Binding("escape", "return_to_menu", "Menu"),
    ]

    CSS = """
    Screen {
        align: center middle;
        background: $surface;
    }

    #session-ended-dialog {
        width: 72%;
        height: auto;
        border: thick $accent;
        padding: 2 4;
        background: $panel;
        content-align: center middle;
    }

    #session-ended-title {
        height: 3;
        text-align: center;
        text-style: bold;
        color: $accent;
    }

    #session-ended-details {
        height: auto;
        text-align: center;
        color: $text;
    }

    #session-ended-help {
        height: 3;
        text-align: center;
        color: $text-muted;
    }
    """

    def __init__(self, result: SshRunResult) -> None:
        super().__init__()
        self.result = result

    def compose(self) -> ComposeResult:
        yield Container(
            Static("SSH SESSION HAS ENDED", id="session-ended-title"),
            Static(self._details(), id="session-ended-details"),
            Static("Press Space or Esc to return to the main menu.", id="session-ended-help"),
            id="session-ended-dialog",
        )

    def action_return_to_menu(self) -> None:
        self.exit()

    def _details(self) -> str:
        lines = [
            f"Profile: {self.result.alias}",
            f"Exit code: {self.result.exit_code}",
            f"TERM: {self.result.term_string}",
        ]
        if self.result.error_message:
            lines.append(f"Reason: {self.result.error_message}")
        return "\n".join(lines)


class HelpScreen(ModalScreen[None]):
    """Keyboard and behavior help."""

    BINDINGS = [
        Binding("escape", "dismiss", "Close"),
        Binding("f10", "quit_app", "Exit"),
    ]

    CSS = """
    HelpScreen {
        align: center middle;
    }

    #help-dialog {
        width: 88%;
        height: 82%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #help-title {
        height: 1;
        text-style: bold;
        color: $accent;
    }

    #help-body {
        height: 1fr;
    }

    #help-footer {
        height: 1;
        color: $text-muted;
    }
    """

    def compose(self) -> ComposeResult:
        yield Container(
            Static("ssh-starter Help", id="help-title"),
            VerticalScroll(Markdown(self._markdown()), id="help-body"),
            Static("Esc close | F10 exit", id="help-footer"),
            id="help-dialog",
        )

    def on_key(self, event: events.Key) -> None:
        if event.key == "escape":
            self.action_dismiss()
            event.prevent_default()
            event.stop()
        elif event.key == "f10":
            self.action_quit_app()
            event.prevent_default()
            event.stop()

    def action_dismiss(self) -> None:
        self.dismiss()

    def action_quit_app(self) -> None:
        self.app.exit()

    @staticmethod
    def _markdown() -> str:
        return """
## Main menu

- `Enter` connects to the selected server with its default profile.
- `F4` opens the user/profile picker.
- `Shift+Enter` also opens the picker when the terminal reports it distinctly from plain `Enter`.
- `Ctrl+Enter` is still accepted when the terminal sends it through. Some terminals encode modified Enter keys as `Ctrl+J`, `Ctrl+M`, or `Ctrl+@`; those are supported too.
- `F1` opens this help.
- `F2` opens options.
- `F3` opens details for the selected server's default profile.
- `F10` exits.
- `Esc` clears an active quick-search filter.
- `Esc Esc` exits when no filter is active.

## Search

Type letters, digits, `-`, `_`, `.`, or `@` to filter the server list.
`Backspace` removes one filter character.

## Grouping

SSH profiles are grouped by visible target and port: `HostName` or alias plus `Port`.
The default profile is selected in this order:

1. users from the `preferred_users` config option;
2. the first sorted profile in the group.

## Profile source

Profiles are imported from your OpenSSH config.
Other than the optional startup sample for an empty config, `ssh-starter` does not create or edit SSH profiles.
To add, rename, or change a profile, edit the SSH config file directly, usually `~/.ssh/config`.

## Options and SSH terminal

`F2` edits the `TERM` string used for launched SSH sessions.
Default: `xterm-256color`.

## Mouse

Mouse selection works when the terminal forwards mouse events:

- click a server to connect with its default profile;
- click a row in the profile picker to select that profile.

## Layout

Tables are fitted to the terminal width and should only scroll vertically.

## About

ssh-starter is released under the MIT License.
Copyright (c) Mykola Rudenko.
""".strip()


class UserSelectionScreen(ModalScreen[str | None]):
    """Profile picker for one grouped server target."""

    BINDINGS = [
        Binding("enter", "select_profile", "Connect"),
        Binding("escape", "dismiss", "Back"),
        Binding("f10", "quit_app", "Exit"),
    ]

    CSS = """
    UserSelectionScreen {
        align: center middle;
    }

    #user-dialog {
        width: 88%;
        height: 76%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #user-title {
        height: 1;
        text-style: bold;
        color: $accent;
    }

    #user-table {
        height: 1fr;
    }

    #user-help {
        height: 1;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        group: SshConnectionGroup,
        history: ConnectionHistory,
        preferred_users: tuple[str, ...],
    ) -> None:
        super().__init__()
        self.group = group
        self.history = history
        self.preferred_users = preferred_users
        self._row_profiles: list[SshProfile] = []

    def compose(self) -> ComposeResult:
        yield Container(
            Static(
                "Choose profile for "
                f"{_default_profile_for_history(self.group, self.history, self.preferred_users).alias} "
                f"({self.group.target_display_name})",
                id="user-title",
            ),
            DataTable(id="user-table"),
            Static("Enter/click connect | Esc back | F10 exit", id="user-help"),
            id="user-dialog",
        )

    def on_mount(self) -> None:
        table = self._table()
        table.cursor_type = "row"
        table.show_row_labels = False
        _configure_fit_table(table)
        table.zebra_stripes = True
        self._row_profiles = list(_sort_profiles_for_history(self.group.profiles, self.history))
        self.call_after_refresh(self._refresh_table)
        table.focus()

    def on_resize(self, event: events.Resize) -> None:
        del event
        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self._table()
        _configure_fit_table(table)
        table.clear(columns=True)
        widths = _weighted_widths(table.size.width or self.size.width, (1, 2, 2, 3))
        table.add_column("User", width=widths[0])
        table.add_column("Alias", width=widths[1])
        table.add_column("Route", width=widths[2])
        table.add_column("Key", width=widths[3])
        for profile in self._row_profiles:
            key_text = ", ".join(profile.identity_files) if profile.identity_files else "default/agent"
            table.add_row(
                _truncate(profile.display_user, widths[0]),
                _truncate(profile.alias, widths[1]),
                _truncate(profile.route_kind.label, widths[2]),
                _truncate(key_text, widths[3]),
                height=1,
            )
        table.scroll_x = 0
        table.show_horizontal_scrollbar = False

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_select_profile()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        event.stop()
        self.action_select_profile()

    def action_select_profile(self) -> None:
        profile = self._selected_profile()
        if profile is None:
            self.notify("No SSH profile selected.", severity="warning")
            return
        self.dismiss(profile.alias)

    def action_dismiss(self) -> None:
        self.dismiss(None)

    def action_quit_app(self) -> None:
        self.app.exit()

    def _selected_profile(self) -> SshProfile | None:
        row_index = self._table().cursor_coordinate.row
        if row_index < 0 or row_index >= len(self._row_profiles):
            return None
        return self._row_profiles[row_index]

    def _table(self) -> DataTable:
        return self.query_one("#user-table", DataTable)


class OptionsScreen(ModalScreen[AppConfig | None]):
    """Runtime options editor."""

    BINDINGS = [
        Binding("escape", "dismiss", "Cancel"),
        Binding("f10", "quit_app", "Exit"),
    ]

    CSS = """
    OptionsScreen {
        align: center middle;
    }

    #options-dialog {
        width: 72%;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 2 4;
    }

    #options-title {
        height: 1;
        text-style: bold;
        color: $accent;
    }

    #term-input {
        margin: 1 0;
    }

    #options-help {
        height: 2;
        color: $text-muted;
    }
    """

    def __init__(self, app_config: AppConfig) -> None:
        super().__init__()
        self.app_config = app_config

    def compose(self) -> ComposeResult:
        yield Container(
            Static("Options", id="options-title"),
            Static("Terminal string for SSH sessions:"),
            Input(value=self.app_config.term_string, placeholder="xterm-256color", id="term-input"),
            Static("Enter save | Esc cancel | F10 exit", id="options-help"),
            id="options-dialog",
        )

    def on_mount(self) -> None:
        self.query_one("#term-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._save_from_input(event.value)

    def action_dismiss(self) -> None:
        self.dismiss(None)

    def action_quit_app(self) -> None:
        self.app.exit()

    def _save_from_input(self, raw_term_string: str) -> None:
        term_string = raw_term_string.strip()
        try:
            updated_config = AppConfig(
                ssh_config_path=self.app_config.ssh_config_path,
                term_string=term_string,
                preferred_users=self.app_config.preferred_users,
            )
        except ValidationError:
            self.notify(
                "Invalid TERM. Use letters, digits, dot, underscore, plus, or hyphen.",
                severity="error",
            )
            self.query_one("#term-input", Input).focus()
            return
        self.dismiss(updated_config)


class SshLauncherApp(App[None]):
    """Full-screen SSH launcher."""

    TITLE = "ssh-starter"
    SUB_TITLE = "SSH profile launcher"

    BINDINGS = [
        Binding("enter", "connect", "Connect"),
        Binding("f4", "choose_user", "Users", priority=True),
        Binding("shift+enter", "choose_user", "Users", priority=True),
        Binding("ctrl+enter", "choose_user", "Users", show=False, priority=True),
        Binding("ctrl+j", "choose_user", "Users", show=False, priority=True),
        Binding("ctrl+m", "choose_user", "Users", show=False, priority=True),
        Binding("ctrl+@", "choose_user", "Users", show=False, priority=True),
        Binding("f1", "help", "Help", priority=True),
        Binding("f2", "options", "Options", priority=True),
        Binding("f3", "profile_info", "Info"),
        Binding("escape", "escape_menu", "Clear filter / exit", show=False),
        Binding("f10", "quit", "Exit"),
    ]

    CSS = """
    Screen {
        layout: vertical;
    }

    #filter-line {
        height: 1;
        background: $panel;
        color: $text;
        padding: 0 1;
    }

    #profile-grid {
        height: 1fr;
    }

    #status-line {
        height: 1;
        background: $accent;
        color: $text;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        inventory: SshInventory,
        app_config: AppConfig,
        app_config_path: Path,
        history: ConnectionHistory | None = None,
    ) -> None:
        super().__init__()
        self.inventory = inventory
        self.app_config = app_config
        self.app_config_path = app_config_path
        self.history = history or ConnectionHistory()
        self.connection_groups = _sort_groups_for_history(
            inventory.connection_groups(),
            self.history,
            self.app_config.preferred_users,
        )
        self.filter_text = ""
        self.filtered_groups: tuple[SshConnectionGroup, ...] = self.connection_groups
        self.selected_alias: str | None = None
        self._last_empty_escape_at: float | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(id="filter-line")
        yield MainProfileGrid(id="profile-grid")
        yield Static(id="status-line")

    def on_mount(self) -> None:
        table = self._table()
        table.cursor_type = "cell"
        table.show_header = False
        table.show_row_labels = False
        _configure_fit_table(table)
        table.zebra_stripes = True
        table.focus()
        self.set_interval(1.0, self._refresh_status)
        self._refresh_profiles(reset_cursor=True)

    def on_resize(self, event: events.Resize) -> None:
        del event
        self._refresh_profiles(reset_cursor=False)

    def on_key(self, event: events.Key) -> None:
        if event.key == "f1":
            self.action_help()
            event.prevent_default()
            event.stop()
            return

        if event.key in USER_PICKER_KEYS:
            self.action_choose_user()
            event.prevent_default()
            event.stop()
            return

        if event.key == "f2":
            self.action_options()
            event.prevent_default()
            event.stop()
            return

        if event.key == "backspace":
            if self.filter_text:
                self._last_empty_escape_at = None
                self.filter_text = self.filter_text[:-1]
                self._refresh_profiles(reset_cursor=True)
                event.prevent_default()
                event.stop()
            return

        if event.character and self._is_search_character(event.character):
            self._last_empty_escape_at = None
            self.filter_text += event.character
            self._refresh_profiles(reset_cursor=True)
            event.prevent_default()
            event.stop()

    def on_data_table_cell_selected(self, event: DataTable.CellSelected) -> None:
        event.stop()
        if self._is_empty_grid_coordinate(event.coordinate.row, event.coordinate.column):
            self._move_cursor_to_last_group()
            return
        self.action_connect()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        self.action_connect()

    def action_connect(self) -> None:
        group = self._selected_group()
        if group is None:
            self._move_cursor_to_last_group()
            return
        self.selected_alias = _default_profile_for_history(
            group,
            self.history,
            self.app_config.preferred_users,
        ).alias
        self.exit()

    def action_choose_user(self) -> None:
        group = self._selected_group()
        if group is None:
            self._move_cursor_to_last_group()
            return
        self.push_screen(
            UserSelectionScreen(group, self.history, self.app_config.preferred_users),
            callback=self._user_selected,
        )

    def action_help(self) -> None:
        self.push_screen(HelpScreen())

    def action_options(self) -> None:
        self.push_screen(OptionsScreen(self.app_config), callback=self._options_updated)

    def action_profile_info(self) -> None:
        group = self._selected_group()
        if group is None:
            self._move_cursor_to_last_group()
            return
        profile = _default_profile_for_history(group, self.history, self.app_config.preferred_users)
        alternatives = self.inventory.alternatives_for(profile)
        warnings = tuple(warning.display() for warning in self.inventory.warnings)
        self.push_screen(ProfileInfoScreen(profile, alternatives, warnings, self.history))

    def action_escape_menu(self) -> None:
        if self.filter_text:
            self.filter_text = ""
            self._last_empty_escape_at = None
            self._refresh_profiles(reset_cursor=True)
            return

        now = monotonic()
        if self._last_empty_escape_at is not None and now - self._last_empty_escape_at <= DOUBLE_ESCAPE_SECONDS:
            self.selected_alias = None
            self.exit()
            return

        self._last_empty_escape_at = now
        self.notify("Press Esc again to exit.", severity="information", timeout=DOUBLE_ESCAPE_SECONDS)
        self._refresh_status()

    def action_quit(self) -> None:
        self.selected_alias = None
        self.exit()

    def _user_selected(self, alias: str | None) -> None:
        if alias is None:
            return
        self.selected_alias = alias
        self.exit()

    def _options_updated(self, updated_config: AppConfig | None) -> None:
        if updated_config is None:
            return
        try:
            updated_config.save(self.app_config_path)
        except OSError as exc:
            self.notify(f"Could not save options: {exc}", severity="error")
            return
        self.app_config = updated_config
        self.notify(f"Options saved. SSH TERM={updated_config.term_string}", severity="information")
        self._refresh_status()

    def _refresh_profiles(self, *, reset_cursor: bool) -> None:
        needle = self.filter_text.lower()
        if needle:
            self.filtered_groups = tuple(
                group for group in self.connection_groups if needle in group.search_text()
            )
        else:
            self.filtered_groups = self.connection_groups

        table = self._table()
        table.clear(columns=True)
        column_widths = self._column_widths()
        for column_index, column_width in enumerate(column_widths):
            table.add_column("", width=column_width, key=f"profile-{column_index}")

        if not self.filtered_groups:
            table.add_row("No matching SSH servers", "", "")
        else:
            for row_profiles in self._profile_rows(self.filtered_groups):
                cells = [
                    self._group_cell(group, column_widths[index]) if group else ""
                    for index, group in enumerate(row_profiles)
                ]
                table.add_row(*cells, height=2)

        if reset_cursor and self.filtered_groups:
            table.move_cursor(row=0, column=0, animate=False)
        table.scroll_x = 0
        self._normalize_cursor()

        self._refresh_status()

    def _refresh_status(self) -> None:
        filter_display = self.filter_text or "none"
        self.query_one("#filter-line", Static).update(f"Filter: {filter_display}")
        self.query_one("#status-line", Static).update(
            "Enter connect | F4 users | F1 help | F2 options | F3 info | "
            "F10/Esc Esc exit | "
            f"Servers {len(self.connection_groups)} total/{len(self.filtered_groups)} shown | "
            f"Profiles {len(self.inventory.profiles)}"
        )

    def _selected_group(self) -> SshConnectionGroup | None:
        if not self.filtered_groups:
            return None
        coordinate = self._table().cursor_coordinate
        index = coordinate.row * GRID_COLUMNS + coordinate.column
        if index < 0 or index >= len(self.filtered_groups):
            return None
        return self.filtered_groups[index]

    def _normalize_cursor(self) -> None:
        if not self.filtered_groups:
            return
        coordinate = self._table().cursor_coordinate
        if self._is_empty_grid_coordinate(coordinate.row, coordinate.column):
            self._move_cursor_to_last_group()

    def _is_empty_grid_coordinate(self, row: int, column: int) -> bool:
        index = row * GRID_COLUMNS + column
        return index < 0 or index >= len(self.filtered_groups)

    def _move_cursor_to_last_group(self) -> None:
        if not self.filtered_groups:
            return
        last_index = len(self.filtered_groups) - 1
        self._table().move_cursor(
            row=last_index // GRID_COLUMNS,
            column=last_index % GRID_COLUMNS,
            animate=False,
        )

    def _table(self) -> DataTable:
        return self.query_one("#profile-grid", DataTable)

    def _column_widths(self) -> tuple[int, int, int]:
        table_width = self._table().size.width or self.size.width
        available_width = max(GRID_COLUMNS, table_width - FIT_HORIZONTAL_RESERVE)
        base_width = max(1, available_width // GRID_COLUMNS)
        remainder = max(0, available_width - (base_width * GRID_COLUMNS))
        first = base_width + (1 if remainder > 0 else 0)
        second = base_width + (1 if remainder > 1 else 0)
        third = base_width
        return first, second, third

    @staticmethod
    def _profile_rows(
        groups: Iterable[SshConnectionGroup],
    ) -> Iterable[tuple[SshConnectionGroup | None, ...]]:
        row: list[SshConnectionGroup | None] = []
        for group in groups:
            row.append(group)
            if len(row) == GRID_COLUMNS:
                yield tuple(row)
                row = []
        if row:
            while len(row) < GRID_COLUMNS:
                row.append(None)
            yield tuple(row)

    def _group_cell(self, group: SshConnectionGroup, width: int) -> Text:
        text = Text()
        default_profile = _default_profile_for_history(group, self.history, self.app_config.preferred_users)
        text.append(_truncate(default_profile.alias, width), style="bold")
        text.append("\n")
        user_line = default_profile.display_user
        if group.profile_count > 1:
            user_line = f"{user_line} (+{group.profile_count - 1})"
        text.append(_truncate(user_line, width), style="dim")
        return text

    @staticmethod
    def _is_search_character(character: str) -> bool:
        return len(character) == 1 and (character.isalnum() or character in SEARCH_EXTRA_CHARS)


def _truncate(value: str, width: int) -> str:
    """Trim text to a single DataTable cell line."""
    if width <= 1:
        return value[:width]
    if len(value) <= width:
        return value
    return f"{value[: width - 1]}…"


def _configure_fit_table(table: DataTable) -> None:
    """Configure a DataTable so content fits horizontally and only vertical scroll is visible."""
    table.cell_padding = 0
    table.show_horizontal_scrollbar = False
    table.show_vertical_scrollbar = True
    table.scroll_x = 0


def _weighted_widths(total_width: int, weights: tuple[int, ...]) -> tuple[int, ...]:
    """Fit weighted column widths inside the current table width."""
    column_count = len(weights)
    available_width = max(column_count, total_width - FIT_HORIZONTAL_RESERVE)
    weight_total = max(1, sum(weights))
    raw_widths = [max(1, (available_width * weight) // weight_total) for weight in weights]

    while sum(raw_widths) > available_width:
        widest_index = max(range(column_count), key=raw_widths.__getitem__)
        if raw_widths[widest_index] <= 1:
            break
        raw_widths[widest_index] -= 1

    index = 0
    while sum(raw_widths) < available_width:
        raw_widths[index % column_count] += 1
        index += 1

    return tuple(raw_widths)


def _sort_groups_for_history(
    groups: tuple[SshConnectionGroup, ...],
    history: ConnectionHistory,
    preferred_users: tuple[str, ...],
) -> tuple[SshConnectionGroup, ...]:
    """Sort server groups by last connection, then alphabetically by default alias."""
    return tuple(sorted(groups, key=lambda group: _group_sort_key(group, history, preferred_users)))


def _group_sort_key(
    group: SshConnectionGroup,
    history: ConnectionHistory,
    preferred_users: tuple[str, ...],
) -> tuple[int, float, str]:
    latest_timestamp = _latest_group_timestamp(group, history)
    default_alias = _default_profile_for_history(group, history, preferred_users).alias.lower()
    if latest_timestamp is None:
        return 1, 0.0, default_alias
    return 0, -latest_timestamp, default_alias


def _latest_group_timestamp(group: SshConnectionGroup, history: ConnectionHistory) -> float | None:
    timestamps = [
        timestamp
        for profile in group.profiles
        if (timestamp := history.timestamp_for(profile.alias)) is not None
    ]
    if not timestamps:
        return None
    return max(timestamps)


def _sort_profiles_for_history(
    profiles: tuple[SshProfile, ...],
    history: ConnectionHistory,
) -> tuple[SshProfile, ...]:
    """Sort profiles by last connection, then alphabetically by alias."""
    return tuple(sorted(profiles, key=lambda profile: _profile_sort_key(profile, history)))


def _profile_sort_key(profile: SshProfile, history: ConnectionHistory) -> tuple[int, float, str]:
    timestamp = history.timestamp_for(profile.alias)
    if timestamp is None:
        return 1, 0.0, profile.alias.lower()
    return 0, -timestamp, profile.alias.lower()


def _default_profile_for_history(
    group: SshConnectionGroup,
    history: ConnectionHistory,
    preferred_users: tuple[str, ...],
) -> SshProfile:
    """Prefer configured users, then the sorted first available profile."""
    sorted_profiles = _sort_profiles_for_history(group.profiles, history)
    normalized_preferred_users = tuple(preferred_user.lower() for preferred_user in preferred_users)
    for preferred_user in normalized_preferred_users:
        for profile in sorted_profiles:
            if profile.display_user.lower() == preferred_user:
                return profile
    return sorted_profiles[0]
