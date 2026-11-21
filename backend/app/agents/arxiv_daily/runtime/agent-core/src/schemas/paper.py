from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Category:
    code: str
    name_cn: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Paper:
    arxiv_id: str
    title: str
    title_cn: str
    summary_cn: str
    pdf_url: str
    authors: str
    affiliations: list[str]
    abstract_cn: str
    abstract: str
    categories: list[str]
    updated: str
    submission_label: str

    def to_dict(self) -> dict:
        return asdict(self)
