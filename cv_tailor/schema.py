from __future__ import annotations

from pydantic import BaseModel, Field

_EMPTY = "Pusty tekst, gdy CV tego nie podaje."


class Contact(BaseModel):
    city: str = Field(description=f"Miasto (bez ulicy). {_EMPTY}")
    email: str = Field(description=f"Adres e-mail. {_EMPTY}")
    phone: str = Field(description=f"Numer telefonu. {_EMPTY}")
    links: list[str] = Field(description="Linki z CV: LinkedIn, GitHub, portfolio, strona; dokładnie jak w CV")


class Experience(BaseModel):
    title: str = Field(description="Stanowisko")
    company: str = Field(description="Firma albo zleceniodawca")
    location: str = Field(description=f"Miejsce pracy. {_EMPTY}")
    start: str = Field(description=f"Data rozpoczęcia w formacie z CV. {_EMPTY}")
    end: str = Field(description=f"Data zakończenia w formacie z CV albo słowo z CV, np. 'obecnie'. {_EMPTY}")
    bullets: list[str] = Field(description="Punkty opisu, każdy osobno, słowo w słowo z CV")


class Project(BaseModel):
    name: str = Field(description="Nazwa projektu")
    description: str = Field(description=f"Krótki opis jednym zdaniem. {_EMPTY}")
    bullets: list[str] = Field(description="Punkty opisu projektu")
    link: str = Field(description=f"Link do projektu. {_EMPTY}")


class SkillGroup(BaseModel):
    category: str = Field(description=f"Nazwa grupy umiejętności. {_EMPTY}")
    items: list[str] = Field(description="Umiejętności w tej grupie")


class Education(BaseModel):
    degree: str = Field(description="Kierunek i stopień")
    school: str = Field(description="Uczelnia albo szkoła")
    location: str = Field(description=f"Miasto. {_EMPTY}")
    start: str = Field(description=f"Data rozpoczęcia. {_EMPTY}")
    end: str = Field(description=f"Data zakończenia albo słowo z CV, np. 'w trakcie'. {_EMPTY}")
    details: list[str] = Field(description="Dodatkowe informacje: specjalizacja, praca dyplomowa, wyróżnienia")


class Language(BaseModel):
    name: str = Field(description="Język")
    level: str = Field(description=f"Poziom jak w CV. {_EMPTY}")


class Certificate(BaseModel):
    name: str = Field(description="Nazwa certyfikatu albo kursu")
    issuer: str = Field(description=f"Wystawca. {_EMPTY}")
    date: str = Field(description=f"Data. {_EMPTY}")


class OtherSection(BaseModel):
    heading: str = Field(description="Nagłówek sekcji z CV, np. Zainteresowania, Wolontariat")
    items: list[str] = Field(description="Pozycje sekcji")


class BaseCv(BaseModel):
    name: str = Field(description="Imię i nazwisko")
    headline: str = Field(description=f"Tytuł zawodowy pod nazwiskiem. {_EMPTY}")
    contact: Contact
    summary: str = Field(description=f"Podsumowanie zawodowe. {_EMPTY}")
    experience: list[Experience]
    projects: list[Project]
    skills: list[SkillGroup]
    education: list[Education]
    languages: list[Language]
    certificates: list[Certificate]
    other: list[OtherSection] = Field(description="Sekcje CV, które nie pasują do pozostałych pól")


class Match(BaseModel):
    must_met: int
    must_total: int
    ratio: float
    recommendation: str


class Requirement(BaseModel):
    term: str
    priority: str = Field(description="must | nice")
    status: str = Field(description="present | added | missing")


class Change(BaseModel):
    path: str = Field(description="Ścieżka JSON w cv, np. experience[0].bullets[1]")
    before: str = Field(description="Poprzedni tekst; pusty dla dodanej pozycji")
    after: str = Field(description="Nowy tekst; pusty dla usuniętej pozycji")
    reason: str


class Question(BaseModel):
    about: str
    question: str
    why: str


class NumberToVerify(BaseModel):
    path: str
    number: str
    question: str


class TailorReply(BaseModel):
    requirements: list[Requirement]
    match: Match
    cv: BaseCv
    changes: list[Change]
    questions: list[Question]
    numbers_to_verify: list[NumberToVerify]
    section_order: list[str]
    file_name: str
