from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Literal

Parity = Literal["odd", "even", "all"]

# Słowa opisujące typ miejscowości, którymi rejestr PKW poprzedza nazwę wsi
# ("wieś Boguszyn", "Sołectwo: X", "przysiółek Y") — do usunięcia przed
# porównaniem z PRG/granicami gmin (patrz parse_opis_granic, gałąź "wieś").
VILLAGE_PREFIX_RE = re.compile(
    # "miejscowo[śs][ćc]i?" obejmuje jednym wzorcem l.poj. ("miejscowość") i l.mn.
    # ("miejscowości") — osobne warianty w alternacji zawodziły, bo regex bierze
    # pierwszą pasującą alternatywę na danej pozycji (krótszą, l.poj.), zostawiając
    # nieusunięte "i:" z l.mn. jako śmieć na początku nazwy.
    r"^(wie[śs]|kolonia|so[łl]ectwo|so[łl]ectwa|przysi[óo][łl]ek|osada|miejscowo[śs][ćc]i?)\s*:?\s*",
    re.I,
)


@dataclass
class NumberRange:
    start: int
    end: int | None  # None = do końca

    def contains(self, number: int) -> bool:
        if number <= 0:
            return False
        if self.end is None:
            return number >= self.start
        return self.start <= number <= self.end


@dataclass
class StreetRule:
    name: str
    parity: Parity = "all"
    ranges: list[NumberRange] = field(default_factory=list)

    def matches(self, street: str, number: int | None) -> bool:
        if not streets_equal(self.name, street):
            return False
        if number is None:
            return not self.ranges and self.parity == "all"
        if self.parity == "odd" and number % 2 == 0:
            return False
        if self.parity == "even" and number % 2 == 1:
            return False
        if not self.ranges:
            return True
        return any(rng.contains(number) for rng in self.ranges)

    def specificity(self) -> int:
        """Im wyższa wartość, tym bardziej precyzyjna reguła (używane przy rozstrzyganiu konfliktów)."""
        if self.ranges:
            return 2
        if self.parity != "all":
            return 1
        return 0


@dataclass
class VillageRule:
    """Reguła "miejscowość + zakresy numerów", np. opis "Borzęcin: 1 - 238,
    412 - 821D". Odpowiednik StreetRule, ale dopasowywana po polu miejscowosc
    (villages_equal), nie po ulicy — potrzebna, bo w gminach wiejskich ta sama
    wieś bywa dzielona między obwody po numerach domów (Borzęcin obwód 2 vs 3)."""

    name: str
    parity: Parity = "all"
    ranges: list[NumberRange] = field(default_factory=list)

    def matches(self, village: str | None, number: int | None) -> bool:
        if not village or not villages_equal(self.name, village):
            return False
        if number is None:
            return not self.ranges and self.parity == "all"
        if self.parity == "odd" and number % 2 == 0:
            return False
        if self.parity == "even" and number % 2 == 1:
            return False
        if not self.ranges:
            return True
        return any(rng.contains(number) for rng in self.ranges)

    def specificity(self) -> int:
        # Wyższa niż samo dopasowanie nazwy wsi (3), żeby przy wsi dzielonej po
        # numerach wygrywał obwód z pasującym zakresem, a nie oba naraz (konflikt).
        return 4 if self.ranges else 3


@dataclass
class ObwodRules:
    obwod: int
    typ_obszaru: str
    raw: str
    locality: str | None = None
    district: str | None = None
    streets: list[StreetRule] = field(default_factory=list)
    villages: list[str] = field(default_factory=list)
    village_rules: list[VillageRule] = field(default_factory=list)

    def matches(self, street: str | None, number: int | None, village: str | None = None) -> bool:
        return self.match_specificity(street, number, village) is not None

    def match_specificity(
        self, street: str | None, number: int | None, village: str | None = None
    ) -> int | None:
        """Zwraca specyficzność najlepiej dopasowanej reguły, albo None jeśli brak dopasowania.

        Obwód może mieć jednocześnie villages i streets (rzadkie hybrydy typu
        "sołectwo X-część, ulice: ...") — sprawdzamy oba i bierzemy lepsze
        dopasowanie, zamiast przerywać na pierwszym niepasującym warunku
        (wcześniejszy kod zwracał None od razu przy niedopasowanej wsi, nigdy
        nie sprawdzając ulic, mimo że oba pola były wypełnione)."""
        specs: list[int] = []
        if self.villages and village and any(villages_equal(v, village) for v in self.villages):
            specs.append(3)
        if self.village_rules and village:
            village_specs = [r.specificity() for r in self.village_rules if r.matches(village, number)]
            if village_specs:
                specs.append(max(village_specs))
        if self.streets and street:
            street_specs = [rule.specificity() for rule in self.streets if rule.matches(street, number)]
            if street_specs:
                specs.append(max(street_specs))
        return max(specs) if specs else None


def normalize_text(value: str) -> str:
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = re.sub(r"\s+", " ", value)
    value = value.replace("ul.", "").replace("ul ", "").replace("al.", "").replace("al ", "")
    value = value.replace("os.", "").replace("pl.", "").replace("rondo ", "")
    # PRG zapisuje ulice Warszawy pełnymi słowami zamiast skrótów: "ulica X",
    # "Aleja/Aleje X", "Trakt X", itd. — bez tego żaden adres z Warszawy nie
    # dopasowywał się do reguł (te używają skrótów "ul."/"al." z rejestru PKW).
    value = value.replace("ulica ", "").replace("aleja ", "").replace("aleje ", "")
    value = value.replace("trakt ", "").replace("rynek ", "").replace("bulwar ", "")
    value = value.replace("skwer ", "").replace("pasaz ", "")
    value = re.sub(r"[\"'„”]", "", value)
    return value.strip(" ,.")


def streets_equal(a: str, b: str) -> bool:
    na, nb = normalize_text(a), normalize_text(b)
    if na == nb:
        return True
    # Dopasowanie po całych słowach (nie po dowolnym podciągu znaków) — luźny substring
    # dawał masowe fałszywe trafienia w gęstej sieci ulic Krakowa (np. "Kolejowa" w
    # "Nowa Kolejowa" ma inny numer administracyjny).
    ta, tb = na.split(), nb.split()
    if not ta or not tb:
        return False
    shorter, longer = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    n = len(shorter)
    return any(longer[i : i + n] == shorter for i in range(len(longer) - n + 1))


def villages_equal(a: str, b: str) -> bool:
    return normalize_text(a) == normalize_text(b)


def split_segments(body: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            segment = "".join(current).strip()
            if segment:
                parts.append(segment)
            current = []
            continue
        current.append(char)
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_range_fragment(fragment: str) -> tuple[list[NumberRange], Parity, str | None]:
    parity: Parity = "all"
    ranges: list[NumberRange] = []
    street_name: str | None = None

    # Dwie konwencje zapisu parzystości: "(parzyste)"/"(nieparzyste)" w nawiasie
    # (większość rejestrów) oraz "strona parzysta"/"strona nieparzysta" bez nawiasu
    # (Warszawa). Uwaga: "nieparzyste" zawiera "parzyste" jako podciąg — sprawdzamy
    # prefiks "nie", a nie samo wystąpienie "parzyst", żeby nie odwrócić parzystości.
    parity_match = re.search(r"\((parzyste|nieparzyste)\)", fragment, re.I)
    if not parity_match:
        parity_match = re.search(r"\bstrona\s+(nieparzyst\w*|parzyst\w*)", fragment, re.I)
    if parity_match:
        word = parity_match.group(1).lower()
        parity = "odd" if word.startswith("nie") else "even"
        fragment = (fragment[: parity_match.start()] + " " + fragment[parity_match.end():]).strip()

    if re.search(r"\bdo ko[nń]ca\b", fragment, re.I):
        nums = re.findall(r"\d+", fragment)
        if nums:
            ranges.append(NumberRange(start=int(nums[-1]), end=None))
        fragment = re.split(r"\bdo ko[nń]ca\b", fragment, flags=re.I)[0].strip(" ,")

    for match in re.finditer(r"(\d+)\s*[-–—]\s*(\d+[a-z]?)", fragment, re.I):
        start = int(re.sub(r"\D", "", match.group(1)))
        end = int(re.sub(r"\D", "", match.group(2)))
        ranges.append(NumberRange(start=min(start, end), end=max(start, end)))
        fragment = fragment.replace(match.group(0), " ")

    # "nr" bywa wstawione przed numerem (Warszawa: "od nr 1 do 33") — opcjonalne.
    for match in re.finditer(r"od\s+(?:nr\s+)?(\d+)\s+do\s+(?:nr\s+)?(\d+[a-z]?)", fragment, re.I):
        start = int(re.sub(r"\D", "", match.group(1)))
        end = int(re.sub(r"\D", "", match.group(2)))
        ranges.append(NumberRange(start=min(start, end), end=max(start, end)))
        fragment = fragment.replace(match.group(0), " ")

    # "od N" bez "do" (Warszawa: "ul. X: od 68") — zakres otwarty od N w górę.
    for match in re.finditer(r"\bod\s+(?:nr\s+)?(\d+[a-z]?)\b", fragment, re.I):
        n = int(re.sub(r"\D", "", match.group(1)))
        ranges.append(NumberRange(start=n, end=None))
        fragment = fragment.replace(match.group(0), " ")

    # Pojedynczy numer zapisany jako "nr N" (bez zakresu) — traktujemy jako zakres N-N.
    for match in re.finditer(r"\bnr\s+(\d+[a-z]?)\b", fragment, re.I):
        n = int(re.sub(r"\D", "", match.group(1)))
        ranges.append(NumberRange(start=n, end=n))
        fragment = fragment.replace(match.group(0), " ")

    fragment = re.sub(r"\s+", " ", fragment).strip(" ,.")
    if fragment:
        street_name = fragment
    return ranges, parity, street_name


def parse_city_description(body: str) -> tuple[str | None, str | None, list[StreetRule]]:
    district = None
    locality = None
    if ":" in body:
        prefix, rest = body.split(":", 1)
        prefix = prefix.strip()
        if "-" in prefix:
            locality, district = prefix.split("-", 1)
            locality = locality.strip()
            district = district.strip()
        else:
            locality = prefix.replace("ulice", "").strip()
        body = rest

    streets: list[StreetRule] = []
    pending_street: StreetRule | None = None

    # Część rejestru (~5% wpisów "miasto" w kraju) rozdziela ulice średnikiem
    # zamiast przecinkiem — bez normalizacji cała lista trafiała jako jeden,
    # nigdy niepasujący "segment" (znalezione przy próbie wygenerowania granic
    # dla całej Polski, np. m. Skarżysko-Kamienna: 8,9% dopasowanych adresów).
    for segment in split_segments(body.replace(";", ",")):
        ranges, parity, street_name = parse_range_fragment(segment)
        if street_name and not ranges and parity == "all":
            pending_street = StreetRule(name=street_name)
            streets.append(pending_street)
            continue

        if street_name:
            streets.append(StreetRule(name=street_name, parity=parity, ranges=ranges))
            pending_street = streets[-1]
        elif (ranges or parity != "all") and pending_street is not None:
            # Ta sama ulica może mieć osobne zakresy dla nieparzystych i parzystych
            # (np. Warszawa: "strona nieparzysta od nr 1 do 33, strona parzysta nr 2,
            # od nr 20 do 26") — jedna StreetRule ma jedno pole parity, więc zmiana
            # parzystości w kontynuacji zaczyna nową regułę dla tej samej ulicy
            # zamiast nadpisywać poprzednią.
            if parity != "all" and parity != pending_street.parity:
                pending_street = StreetRule(name=pending_street.name, parity=parity, ranges=list(ranges))
                streets.append(pending_street)
            else:
                pending_street.ranges.extend(ranges)
                if parity != "all":
                    pending_street.parity = parity
        elif segment.strip():
            streets.append(StreetRule(name=segment.strip()))

    return locality, district, streets


def _parse_warszawa_clause(clause: str) -> tuple[str | None, list[NumberRange], Parity]:
    """Parsuje pojedynczą klauzulę numeryczną Warszawy (fragment po dwukropku,
    ewentualnie po rozbiciu na "i"/"oraz"), zwraca (nazwa_ulicy_lub_None, zakresy,
    parzystość)."""
    ranges, parity, leftover = parse_range_fragment(clause)
    if not leftover:
        return None, ranges, parity
    # Parzystość bywa zapisana gołym słowem, bez "strona" i bez nawiasu
    # (np. "nieparzyste od 135 do 197", "od 2 do 92R parzyste").
    if parity == "all":
        parity_match = re.search(r"\b(nieparzyste|parzyste)\b", leftover, re.I)
        if parity_match:
            word = parity_match.group(1).lower()
            parity = "odd" if word.startswith("nie") else "even"
            leftover = (leftover[: parity_match.start()] + " " + leftover[parity_match.end():]).strip()
    # Gołe numery bez słowa "nr" na końcu fragmentu (np. "ul. Oławska 1", "156").
    number_match = re.search(r"^(.*?)\b(\d+[a-z]?(?:\s*,\s*\d+[a-z]?)*)\s*$", leftover, re.I)
    street_part = leftover
    if number_match and number_match.group(2):
        for num in re.finditer(r"\d+[a-z]?", number_match.group(2), re.I):
            n = int(re.sub(r"\D", "", num.group(0)))
            ranges.append(NumberRange(start=n, end=n))
        street_part = number_match.group(1).strip()
    street_part = street_part.strip(" ,.")
    return (street_part or None), ranges, parity


def parse_warszawa_description(text: str) -> list[StreetRule]:
    """Warszawa opisuje granice obwodów w formacie "ul. X: specyfikacja numerów"
    (segmenty rozdzielone przecinkami i średnikami), zamiast prostej listy ulic
    używanej przez inne miasta — stąd osobny parser. Specyfikacja może być:
    "cała" (cała ulica), pojedynczy numer, zakres ("od X do Y"), zakres z
    parzystością, albo kilka klauzul połączonych słowem "i"/"oraz" (osobne
    zakresy dla nieparzystych i parzystych)."""
    streets: list[StreetRule] = []
    bare_number = re.compile(r"^\d+[a-z]?$", re.I)

    # Warszawa łączy numery sąsiadujących budynków ukośnikiem (np. "1/3", "5/7")
    # — traktujemy to jako dwa osobne numery, nie jeden nieparsowalny token.
    text = re.sub(r"(\d+[a-z]?)/(\d+[a-z]?)", r"\1, \2", text, flags=re.I)

    for raw_segment in split_segments(text.replace(";", ",")):
        segment = raw_segment.strip()
        if not segment:
            continue

        if ":" in segment:
            name_part, spec_part = segment.split(":", 1)
            name_part = name_part.strip()
            spec_part = spec_part.strip()
            if not name_part:
                continue
            if not spec_part or re.fullmatch(r"cał[ae]", spec_part, re.I):
                streets.append(StreetRule(name=name_part))
                continue
            for clause in re.split(r"\s+(?:i|oraz)\s+", spec_part, flags=re.I):
                clause = clause.strip(" ,.")
                if not clause:
                    continue
                _, ranges, parity = _parse_warszawa_clause(clause)
                streets.append(StreetRule(name=name_part, parity=parity, ranges=ranges))
            continue

        if bare_number.match(segment) and streets:
            n = int(re.sub(r"\D", "", segment))
            streets[-1].ranges.append(NumberRange(start=n, end=n))
            continue

        street_name, ranges, parity = _parse_warszawa_clause(segment)
        if street_name:
            streets.append(StreetRule(name=street_name, parity=parity, ranges=ranges))
        elif ranges and streets:
            last = streets[-1]
            if parity != "all" and parity != last.parity:
                streets.append(StreetRule(name=last.name, parity=parity, ranges=list(ranges)))
            else:
                last.ranges.extend(ranges)
                if parity != "all":
                    last.parity = parity

    return streets


# Grupa sołectwa z listą miejscowości w nawiasie. Dwie odmiany w rejestrze:
#   "Sołectwo X (miejscowości: A, B)"  — nazwa X to jednostka administracyjna,
#      która NIE występuje w PRG; liczy się wyłącznie lista po "miejscowości:".
#   "Chełchy (Chełchy, Czaple)"        — goły nawias bez słowa "miejscowości";
#      tu nazwa przed nawiasem TEŻ bywa miejscowością (sołectwo = wieś główna),
#      więc dodajemy i nazwę, i zawartość nawiasu.
# PAREN_GROUP_RE łapie obie; obecność "miejscowości:" rozstrzyga, czy nazwę
# przed nawiasem pominąć.
VILLAGE_GROUP_RE = re.compile(r"^(.*?)\(\s*miejscowo[śs]ci\s*:\s*(.*?)\s*\)\s*$", re.I)
PAREN_GROUP_RE = re.compile(r"^(.*?)\(\s*(.*?)\s*\)\s*$", re.S)
# Zawartość nawiasu to numery/zakresy, a nie lista wsi (np. "Kwiatowa (od 1 do 9)")
# — wtedy NIE traktujemy jej jako miejscowości (to raczej ulica z zakresem).
_LOOKS_NUMERIC_RE = re.compile(r"^\s*(nr\b|od\b|do\b|\d)", re.I)


def parse_wies_description(text: str) -> tuple[list[str], list["VillageRule"], list[StreetRule]]:
    """Parsuje opis granic dla typ_obszaru "wieś". Formaty spotykane w rejestrze:
    - prosta lista: "Nowa Wieś, Stara Wieś",
    - z prefiksem typu miejscowości: "wieś X", "kolonia Y", "Sołectwo: Z",
    - grupy sołectw z osobną listą miejscowości: "Sołectwa: A (miejscowości:
      P, Q); B (miejscowości: R)" — separator ";" LUB ",", nawias chroni
      wewnętrzną listę przed rozbiciem,
    - fragmenty sołectwa z listą ulic: "Sołectwo X-część, ulice: A, B" — rzadkie,
      ale spotykane; ulice trafiają do `streets`, nazwa sołectwa (jeśli coś
      zostanie po odcięciu "-część"/prefiksu) do `villages` na wszelki wypadek.
    """
    villages: list[str] = []
    village_rules: list[VillageRule] = []
    streets: list[StreetRule] = []
    # Zakresy numerów wsi ("Borzęcin: 1 - 238, 412 - 821D") split_segments rozbija
    # po przecinku na osobne segmenty, gdzie tylko pierwszy ma nazwę — pending_village
    # trzyma bieżącą wieś, by doczepić do niej kolejne, bezimienne zakresy (analogicznie
    # do pending_street w parse_city_description).
    pending_village: VillageRule | None = None
    # Po napotkaniu "ulice:" WSZYSTKIE kolejne segmenty (aż do końca opisu) są
    # nazwami ulic, nie miejscowości — split_segments już rozbił je na osobne
    # segmenty przecinkami, więc trzeba pamiętać ten stan między iteracjami,
    # zamiast sprawdzać "ulice:" tylko w obrębie pojedynczego segmentu.
    in_streets_mode = False

    # Semicolon i przecinek pełnią tu tę samą rolę (separator grup na poziomie
    # najwyższym) — normalizujemy do przecinka i używamy split_segments, który
    # już respektuje głębokość nawiasów (patrz parser Warszawy).
    for segment in split_segments(text.replace(";", ",")):
        segment = segment.strip(" .")
        if not segment:
            continue

        if in_streets_mode:
            streets.append(StreetRule(name=segment))
            continue

        ulice_match = re.search(r"\bulice\s*:\s*", segment, re.I)
        if ulice_match:
            before = segment[: ulice_match.start()]
            after = segment[ulice_match.end() :]
            before = VILLAGE_PREFIX_RE.sub("", before.strip()).strip(" .-")
            if before:
                villages.append(before)
            if after.strip():
                streets.append(StreetRule(name=after.strip()))
            in_streets_mode = True
            continue

        paren_match = PAREN_GROUP_RE.match(segment)
        if paren_match and not _LOOKS_NUMERIC_RE.match(paren_match.group(2)):
            pending_village = None
            name_part = paren_match.group(1).strip()
            inner = paren_match.group(2).strip()
            miejsc = re.match(r"miejscowo[śs]ci\s*:\s*(.*)$", inner, re.I | re.S)
            if miejsc:
                # "Sołectwo X (miejscowości: A, B)" — X to jednostka admin, pomijamy.
                inner = miejsc.group(1)
            else:
                # "Chełchy (Chełchy, Czaple)" — nazwa sołectwa też bywa miejscowością.
                nm = VILLAGE_PREFIX_RE.sub("", name_part).strip(" .")
                if nm:
                    villages.append(nm)
            for name in split_segments(inner):
                name = VILLAGE_PREFIX_RE.sub("", name.strip()).strip(" .")
                if name:
                    villages.append(name)
            continue

        # Format "Wieś: zakresy" (i kontynuacje). Najpierw zdejmujemy prefiks typu
        # miejscowości ("Miejscowości: X", "sołectwa: Y") — inaczej dzielenie po ":"
        # wzięłoby prefiks za nazwę. Po zdjęciu prefiksu ":" zostaje już tylko w
        # formacie "Nazwa: nr budynków 1 - 9" (nazwa wsi PRZED dwukropkiem).
        stripped = VILLAGE_PREFIX_RE.sub("", segment).strip(" .")
        ranges, parity, raw_name = parse_range_fragment(stripped)
        raw_name = raw_name or ""
        if ":" in raw_name:
            raw_name = raw_name.split(":", 1)[0]
        # "nr budynków"/"numery" to szum między nazwą a zakresami.
        clean_name = re.sub(
            r"\b(nr\s+budynk\w*|numery|numer|nr)\b", "", raw_name.strip(" :."), flags=re.I
        ).strip(" .,")

        if clean_name and (ranges or parity != "all"):
            pending_village = VillageRule(name=clean_name, parity=parity, ranges=list(ranges))
            village_rules.append(pending_village)
        elif (ranges or parity != "all") and pending_village is not None:
            # Kontynuacja zakresów tej samej wsi; zmiana parzystości zaczyna nową
            # regułę (jedna VillageRule ma jedno pole parity) — jak w parse_city_description.
            if parity != "all" and parity != pending_village.parity:
                pending_village = VillageRule(name=pending_village.name, parity=parity, ranges=list(ranges))
                village_rules.append(pending_village)
            else:
                pending_village.ranges.extend(ranges)
                if parity != "all":
                    pending_village.parity = parity
        elif clean_name:
            pending_village = None
            villages.append(clean_name)

    return villages, village_rules, streets


def parse_opis_granic(obwod: int, typ_obszaru: str, raw: str) -> ObwodRules:
    text = raw.strip()
    rules = ObwodRules(obwod=obwod, typ_obszaru=typ_obszaru, raw=text)

    if typ_obszaru == "wieś" or (typ_obszaru == "miasto i wieś" and "ulice" not in text.lower()):
        # Rejestr PKW poprzedza nazwy miejscowości słowem opisującym ich typ
        # ("wieś Boguszyn", "kolonia Gaj", "Sołectwo: X", "przysiółek Y") — ale
        # PRG (miejscowosc) i granice gmin mają samą nazwę bez tego prefiksu.
        # Bez usunięcia go żadna miejscowość się nie dopasowywała (0% adresów
        # dla większości gmin wiejskich w kraju — wykryte dopiero przy próbie
        # wygenerowania granic dla całej Polski, bo Kraków i 21 wcześniej
        # wygenerowanych miast są typu "miasto", nie dotykają tej gałęzi).
        # Niektóre regiony (~6% wpisów "wieś" w kraju) grupują miejscowości pod
        # sołectwami z osobną listą w nawiasie ("Sołectwo X (miejscowości: A,
        # B)") albo mieszają sołectwo z listą ulic — patrz parse_wies_description.
        villages, village_rules, streets = parse_wies_description(text)
        rules.villages = villages
        rules.village_rules = village_rules
        rules.streets = streets
        return rules

    if typ_obszaru == "dzielnica w m.st. Warszawa":
        # Warszawa opisuje granice w formacie "ul. X: specyfikacja numerów"
        # (patrz parse_warszawa_description), zupełnie inaczej niż pozostałe
        # miasta — musi być rozpoznana przed ogólną heurystyką "ulice:"/"-"
        # poniżej, bo nazwy ulic bywają myślnikowe i fałszywie by ją triggerowały.
        rules.streets = parse_warszawa_description(text)
        return rules

    if "ulice:" in text.lower() or "-" in text.split(":")[0]:
        locality, district, streets = parse_city_description(text)
        rules.locality = locality
        rules.district = district
        rules.streets = streets
        return rules

    if typ_obszaru == "miasto":
        rules.streets = [StreetRule(name=part.strip()) for part in split_segments(text) if part.strip()]
        return rules

    rules.villages = [text]
    return rules


def resolve_obwod(
    rules_list: list[ObwodRules],
    street: str | None,
    number: int | None,
    village: str | None = None,
) -> tuple[int | None, int]:
    """Zwraca (obwod albo None, liczba surowych dopasowań przed rozstrzygnięciem konfliktu).

    Gdy adres pasuje do wielu obwodów, wygrywa ten z najbardziej specyficzną regułą
    (zakres numerów > parzystość > cała ulica / miejscowość). Jeśli po tym kryterium
    nadal jest remis, zwracane jest None (prawdziwa niejednoznaczność).
    """
    scored = [
        (rules.obwod, rules.match_specificity(street, number, village)) for rules in rules_list
    ]
    scored = [(obwod, spec) for obwod, spec in scored if spec is not None]
    if not scored:
        return None, 0
    max_spec = max(spec for _, spec in scored)
    top = [obwod for obwod, spec in scored if spec == max_spec]
    winner = top[0] if len(top) == 1 else None
    return winner, len(scored)


def assign_obwod(
    rules_list: list[ObwodRules],
    street: str | None,
    number: int | None,
    village: str | None = None,
) -> int | None:
    winner, _ = resolve_obwod(rules_list, street, number, village)
    return winner
