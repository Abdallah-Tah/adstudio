"""Load versioned prompt .md files from stages/prompts/.

Prompts use $name placeholders (string.Template) so Abdallah can edit the
.md files directly without worrying about Python brace escaping.
"""
from pathlib import Path
from string import Template

PROMPTS_DIR = Path(__file__).parent / "prompts"


def load_prompt(name: str, **values: str) -> str:
    text = (PROMPTS_DIR / f"{name}.md").read_text()
    return Template(text).substitute(**values)
