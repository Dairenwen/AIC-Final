from pathlib import Path

from app.services.skill_importer import (
    _github_reference,
    download_skill_content,
    load_crawled_skills,
)


class FakeResponse:
    def __init__(self, *, payload=None, content=b"", content_type="text/plain"):
        self._payload = payload
        self.content = content
        self.status_code = 200
        self.headers = {"content-type": content_type}
        self.text = content.decode("utf-8")

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeSession:
    def get(self, url, **kwargs):
        if "api.github.com" in url:
            return FakeResponse(
                payload={
                    "sha": "revision-1",
                    "tree": [
                        {"path": "skills/demo/SKILL.md", "type": "blob"},
                        {"path": "skills/demo/references/guide.md", "type": "blob"},
                        {"path": "skills/demo/image.png", "type": "blob"},
                    ],
                },
                content_type="application/json",
            )
        return FakeResponse(content=f"content for {url}".encode("utf-8"))


def test_github_tree_downloads_all_text_files_under_skill_path():
    result = download_skill_content(
        "https://github.com/acme/research/tree/main/skills/demo",
        session=FakeSession(),
        timeout=1,
    )

    assert result["status"] == "DOWNLOADED"
    assert set(result["files"]) == {"skills/demo/SKILL.md", "skills/demo/references/guide.md"}
    assert result["file_count"] == 2
    assert "SKILL.md" in result["full_content"]


def test_crawled_skill_file_supports_json_and_github_reference(tmp_path: Path):
    source = tmp_path / "skills.json"
    source.write_text('{"items": [{"title": "demo", "description": "full"}]}', encoding="utf-8")

    assert load_crawled_skills(source)[0]["title"] == "demo"
    assert _github_reference("https://github.com/acme/repo/tree/main/skills/demo") == {
        "owner": "acme",
        "repo": "repo",
        "ref": "main",
        "path": "skills/demo",
    }
