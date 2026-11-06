from pathlib import Path

from scraper_api.app.core.config import ScraperConfig
from scraper_api.app.core.context import Ctx
from scraper_api.app.core.history import parse_git_log
from scraper_api.app.core.scan import classify, collect_files, should_ignore
from scraper_api.app.core.text_utils import leading_block, match_brace, split_top_level


def test_config_defaults_match_notebook() -> None:
    config = ScraperConfig()

    assert config.test_features == frozenset({"accounts"})
    assert config.max_tokens == 2048
    assert config.include_path_hint is True
    assert config.mutation_kinds == ("url_typo", "wrong_verb", "drop_field")
    assert config.allowed_extensions == frozenset({".cs", ".ts", ".html", ".scss", ".css", ".md"})


def test_context_state_is_not_shared() -> None:
    first = Ctx(root=Path("repo-a"), config=ScraperConfig())
    second = Ctx(root=Path("repo-b"), config=ScraperConfig())

    first.texts["one.cs"] = "class One {}"
    first.files.append(Path("one.cs"))

    assert second.texts == {}
    assert second.files == []


def test_scan_uses_notebook_ignore_and_classification_rules(tmp_path: Path) -> None:
    config = ScraperConfig()
    (tmp_path / "src" / "Controllers").mkdir(parents=True)
    (tmp_path / "src" / "Controllers" / "UsersController.cs").write_text("class UsersController {}")
    (tmp_path / "src" / "users.component.ts").write_text("export class UsersComponent {}")
    (tmp_path / "src" / "users.component.spec.ts").write_text("describe('users', () => {});")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "ignored.ts").write_text("export const ignored = true;")
    (tmp_path / "appsettings.Development.json").write_text("{}")

    files = collect_files(tmp_path, config)
    relative = [path.relative_to(tmp_path).as_posix() for path in files]

    assert relative == [
        "src/Controllers/UsersController.cs",
        "src/users.component.spec.ts",
        "src/users.component.ts",
    ]
    assert classify(relative[0]) == "cs_controller"
    assert classify(relative[1]) == "ts_spec"
    assert classify(relative[2]) == "ts_component"
    assert should_ignore(tmp_path / "appsettings.Development.json", tmp_path, config)


def test_parse_git_log_uses_unit_separator_and_keeps_pipes() -> None:
    raw = (
        "@@0123456789abcdef\x1fAuthor | Team\x1f2025-01-02T03:04:05+00:00\x1fFix | preserve value\n"
        "src/example.ts\n"
    )

    history = parse_git_log(raw)

    commit = history["src/example.ts"][0]
    assert commit.short_hash == "0123456"
    assert commit.author == "Author | Team"
    assert commit.subject == "Fix | preserve value"


def test_match_brace_skips_nested_braces_in_comments_and_strings() -> None:
    text = 'public void Run() { var text = "}"; /* { */ return; } trailing'
    opening = text.index("{")

    assert text[match_brace(text, opening)] == "}"


def test_split_top_level_preserves_nested_separators() -> None:
    value = 'a, call(x, y), { key: "x,y" }, Result<Left, Right>'

    assert split_top_level(value) == [
        "a",
        "call(x, y)",
        '{ key: "x,y" }',
        "Result<Left, Right>",
    ]


def test_leading_block_collects_attached_attributes_and_comments() -> None:
    text = "// endpoint\n[HttpGet(\"users\")]\npublic void GetUsers() {}\n"
    declaration = text.index("public void")

    start, attributes = leading_block(text, declaration)

    assert start == 0
    assert attributes == ["// endpoint", "[HttpGet(\"users\")]"]