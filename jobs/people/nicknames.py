"""Common English first-name nickname equivalences, used by jobs/people/lookup.py
to narrow an ambiguous last-name-only match down to the person actually meant
when the asker used a nickname or short form instead of the name on file.

Bug found 2026-09-14: Bill asked Watson "When was the last time Jen DiMatteo
came to church?" -- lookup.py's cascade found no match for the literal query,
fell back to matching "DiMatteo" alone, and returned all three DiMatteo
household members (Gerry, Jennifer, Sophia) as an ambiguous match, discarding
"Jen" entirely instead of using it to pick Jennifer. A literal substring check
against "Jen" would have worked for THIS pair (it's already inside
"Jennifer"), but most nickname/full-name pairs aren't substrings of each
other ("Bill"/"William", "Peggy"/"Margaret", "Jack"/"John"), so a real
equivalence table is needed for the general case, not just this one name.

Not exhaustive -- covers the common English names most likely to appear in a
church directory. Add more here as real mismatches surface."""

# canonical (lowercase) first name -> set of common nicknames (lowercase)
_CANONICAL_TO_NICKNAMES: dict[str, set[str]] = {
    "william": {"bill", "billy", "will", "willy", "liam"},
    "robert": {"bob", "bobby", "rob", "robbie"},
    "richard": {"rick", "ricky", "dick", "rich", "richie"},
    "james": {"jim", "jimmy", "jamie"},
    "john": {"jack", "johnny"},
    "joseph": {"joe", "joey"},
    "charles": {"charlie", "chuck", "chas"},
    "thomas": {"tom", "tommy"},
    "michael": {"mike", "mikey", "mick"},
    "christopher": {"chris"},
    "daniel": {"dan", "danny"},
    "matthew": {"matt"},
    "anthony": {"tony"},
    "edward": {"ed", "eddie", "ted", "teddy"},
    "donald": {"don", "donnie"},
    "ronald": {"ron", "ronnie"},
    "kenneth": {"ken", "kenny"},
    "steven": {"steve", "stevie"},
    "stephen": {"steve", "stevie"},
    "gerald": {"gerry", "jerry"},
    "gerard": {"gerry", "jerry"},
    "raymond": {"ray"},
    "samuel": {"sam", "sammy"},
    "nicholas": {"nick", "nicky"},
    "andrew": {"andy", "drew"},
    "benjamin": {"ben", "benny"},
    "timothy": {"tim", "timmy"},
    "alexander": {"alex", "al"},
    "frederick": {"fred", "freddie"},
    "lawrence": {"larry"},
    "harold": {"harry", "hal"},
    "walter": {"walt"},
    "gregory": {"greg"},
    "peter": {"pete"},
    "patrick": {"pat", "paddy"},
    "philip": {"phil"},
    "phillip": {"phil"},
    "douglas": {"doug"},
    "russell": {"russ"},
    "nathaniel": {"nate", "nat"},
    "jonathan": {"jon", "jonny"},
    "zachary": {"zach", "zack"},
    "theodore": {"ted", "teddy", "theo"},
    "kevin": {"kev"},
    "george": {"georgie"},
    "arthur": {"art", "artie"},
    "eugene": {"gene"},
    "francis": {"frank", "frankie"},
    "franklin": {"frank", "frankie"},
    "leonard": {"leo", "lenny"},

    "jennifer": {"jen", "jenny", "jennie", "jenn"},
    "elizabeth": {"liz", "lizzie", "beth", "betty", "betsy", "eliza"},
    "katherine": {"kate", "katie", "kathy", "kat", "kit"},
    "catherine": {"cate", "cathy", "katie", "kate", "kit"},
    "margaret": {"maggie", "meg", "peggy", "marge", "greta"},
    "patricia": {"pat", "patty", "tricia", "trish"},
    "susan": {"sue", "susie", "suzy"},
    "deborah": {"deb", "debbie"},
    "barbara": {"barb", "barbie", "bobbie"},
    "cynthia": {"cindy"},
    "sandra": {"sandy"},
    "theresa": {"terry", "tessa", "teri"},
    "victoria": {"vicki", "vicky", "tori"},
    "rebecca": {"becky", "becca"},
    "samantha": {"sam", "sammy"},
    "christine": {"chris", "christy", "tina"},
    "christina": {"chris", "christy", "tina"},
    "jacqueline": {"jackie", "jacky"},
    "kimberly": {"kim"},
    "melissa": {"missy", "lisa"},
    "michelle": {"shelly", "shell"},
    "stephanie": {"steph"},
    "veronica": {"ronnie", "vera"},
    "virginia": {"ginny", "ginger"},
    "dorothy": {"dot", "dotty", "dolly"},
    "gloria": {"glo"},
    "judith": {"judy"},
    "carolyn": {"carol", "carrie"},
    "caroline": {"carol", "carrie"},
    "alexandra": {"alex", "sandra", "sandy", "lexi"},
    "amanda": {"mandy"},
    "angela": {"angie"},
    "danielle": {"dani"},
    "gabrielle": {"gabby"},
    "isabella": {"bella", "izzy"},
    "olivia": {"liv", "livvy"},
    "natalie": {"nat"},
    "valerie": {"val"},
    "vanessa": {"nessa"},
    "roberta": {"bobbie", "robbie"},
    "wilhelmina": {"billie", "mina"},
}

_NICKNAME_TO_CANONICAL: dict[str, set[str]] = {}
for _canon, _nicks in _CANONICAL_TO_NICKNAMES.items():
    for _nick in _nicks:
        _NICKNAME_TO_CANONICAL.setdefault(_nick, set()).add(_canon)


def equivalent_first_names(word: str) -> set[str]:
    """Given one lowercase word from a free-typed query, return the set of
    first names (lowercase) it should be treated as equivalent to for
    matching purposes: itself, plus (if it's a canonical name) its common
    nicknames, plus (if it's a nickname) the canonical name(s) it's short
    for. Always includes `word` itself, so callers can use this
    unconditionally with no separate fallback branch."""
    word = (word or "").strip().lower()
    out = {word}
    out |= _CANONICAL_TO_NICKNAMES.get(word, set())
    out |= _NICKNAME_TO_CANONICAL.get(word, set())
    return out
