from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScraperConfig:
    ignore_dirs: frozenset[str] = frozenset({
        ".git", ".github", ".vs", ".vscode", ".idea", "bin", "obj", "packages",
        "testresults", "publish", "node_modules", "dist", ".angular", "coverage",
        "out-tsc", ".nx", ".cache", "migrations", "__pycache__", ".venv", "venv",
    })
    ignore_file_patterns: tuple[str, ...] = (
        "*.g.cs", "*.g.i.cs", "*.designer.cs", "*.generated.cs", "*.assemblyinfo.cs", "*modelsnapshot.cs",
        "*.min.js", "*.min.css", "*.map", "*.bundle.js",
        "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "*.lock",
        "appsettings*.json", ".env", ".env.*", "*.pfx", "*.key", "*.pem", "*.p12", "secrets.json",
        "launchsettings.json", "*.user", "*.csproj", "*.sln",
        "*.dll", "*.exe", "*.pdb", "*.png", "*.jpg", "*.jpeg", "*.gif", "*.svg", "*.ico", "*.woff", "*.woff2",
        "*.ttf", "*.eot", "*.pdf", "*.zip", "*.log", "*.tmp", "*.bak", ".ds_store", "thumbs.db",
        "polyfills.ts", "main.ts", "test.ts", "environment*.ts",
    )
    allowed_extensions: frozenset[str] = frozenset({".cs", ".ts", ".html", ".scss", ".css", ".md"})
    max_file_bytes: int = 200_000
    test_features: frozenset[str] = frozenset({"accounts"})
    max_tokens: int = 2048
    tokenizer_model: str = "Qwen/Qwen2.5-Coder-0.5B-Instruct"
    mutation_kinds: tuple[str, ...] = ("url_typo", "wrong_verb", "drop_field")
    include_path_hint: bool = True
    use_git: bool = True
    system_msg: str = (
        "You are a coding assistant for the ACWA project. Stack: ASP.NET Core Web API (C#) and an "
        "Angular client that uses the legacy @angular/http service style. Follow the project's conventions."
    )
    max_mutations: int = 10_000