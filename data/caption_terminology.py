"""Exact Danbooru terminology anchors for natural-language captions.

Matching ground-truth tags must appear verbatim in the caption so tag and
natural-language training use the same wording.
"""

# Source: https://danbooru.donmai.us/wiki_pages/tag_group%3Asex_acts
# Source revision checked: 2026-07-27. Broad appearance and identity labels
# from the page are intentionally excluded; this list covers acts and act states.
EXACT_CAPTION_TERMS = frozenset(
    line.strip()
    for line in """
presenting own body
take your pick
footjob
double footjob
implied footjob
cooperative footjob
footjob with footwear
footjob with shoes
footjob with boots
footjob with sandals
footjob with legwear
two-footed footjob
reverse footjob
footjob from behind
female footjob
footjob under table
simulated footjob
after footjob
licking foot
foot worship
smelling feet
frottage
armpit sex
pussyjob
backjob
buttjob
cooperative pussyjob
kneepit sex
legjob
paizuri
autopaizuri
cooperative paizuri
handsfree paizuri
naizuri
paizuri over clothes
paizuri on lap
paizuri under clothes
pecjob
perpendicular paizuri
stealth paizuri
straddling paizuri
sideways perpendicular paizuri
thigh sex
glansjob
cloth glansjob
groping
grabbing another's ass
grabbing another's breast
guided breast grab
pectoral grab
guided pectoral grab
nipple tweak
crotch grab
guided crotch grab
grabbing own breast
torso grab
hairjob
handjob
caressing testicles
double handjob
cooperative handjob
nursing handjob
cuddling handjob
reverse nursing handjob
reach-around
two-handed handjob
masturbation
crotch rub
building sex
pillow humping
teddy bear sex
table humping
female masturbation
futanari masturbation
implied masturbation
male masturbation
masturbation through clothes
masturbation under clothes
mutual masturbation
stealth masturbation
tail masturbation
tail insertion
tailjob
oral
anilingus
rusty trombone
breast sucking
cunnilingus
autocunnilingus
implied cunnilingus
fellatio
autofellatio
cum swap
deepthroat
implied fellatio
irrumatio
cooperative fellatio
multiple penis fellatio
69
upright 69
hug and suck
licking testicle
sitting on face
testicle sucking
licking armpit
group sex
oral sandwich
daisy chain (sex)
gangbang
double penetration
triple penetration
love train
cooperative breast smother
orgy
reverse spitroast
spitroast
teamwork (sexual)
threesome
mmf threesome
ffm threesome
mmm threesome
fff threesome
fdd threesome
mdd threesome
ffd threesome
mmd threesome
ddd threesome
mfd threesome
mmc threesome
ffc threesome
mcc threesome
ccc threesome
mmo threesome
ffo threesome
oof threesome
oom threesome
mfo threesome
foursome
fivesome
animal insertion
cervical penetration
covered penetration
deep penetration
food insertion
cum inflation
enema
large insertion
stomach bulge
male penetrated
multiple insertions
nipple penetration
nosejob
object insertion
vaginal object insertion
anal object insertion
urethral insertion
sounding
fingering
anal fingering
prostate milking
fingering through clothes
fingering through panties
implied fingering
fisting
anal fisting
self fisting
double fisting
sex
after sex
after anal
after buttjob
after fellatio
after fingering
after frottage
after insertion
after masturbation
after oral
after paizuri
after rape
after urethral
after vaginal
afterglow
clothed after sex
anal
double anal
imminent anal
pegging
triple anal
multiple anal
clothed sex
guided penetration
happy sex
imminent penetration
implied sex
navel sex
rough sex
sex from behind
skull fucking
ear sex
eye sex
tentacle sex
underwater sex
vaginal
double vaginal
imminent vaginal
triple vaginal
bulges touching
penises touching
testicles touching
tribadism
breast smother
lactation
breastfeeding
lactation through clothes
milking machine
breast pump
strangling
bondage
breast bondage
shibari
shibari over clothes
shibari under clothes
suspension
body writing
public use
spanked
clitoris torture
nipple torture
nipple clamps
nipple pull
ball busting
tickle torture
fire play
ice play
wax play
cum
bukkake
cumdump
cum bath
cumdrip
cum pool
cum in ass
cum in clothes
cum in cup
cum in mouth
cum in throat
cum on tongue
cum in pussy
cum in urethra
cum on body
cum on armpits
cum on ass
cum on back
cum on breasts
cum on chest
cum on feet
cum on fingers
cum on hair
cum on pectorals
cum on pussy
cum on stomach
cum in navel
cum on clothes
cum on eyewear
cum on food
ejaculation
ejaculating while penetrated
pull out
facial
autofacial
felching
gokkun
public indecency
public nudity
public vibrator
stealth bondage
stealth sex
rape
assisted rape
imminent rape
molestation
sleep molestation
chikan
compensated molestation
internal cumshot
used condom on penis
condom left inside
used condom
defloration
female ejaculation
pussy juice
glory hole
glory wall
mating (animal)
giving birth
impregnation
unbirthing
consensual tentacles
tentacle gagged
tentacle on penis
tentacles under clothes
tentacles on male
versatile slime penetration
scat
peeing
drinking pee
golden shower
peeing on viewer
peeing self
doggystyle
cowgirl position
reverse cowgirl position
standing sex
butt plug
""".splitlines()
    if line.strip()
)

# Explicit anatomy and censorship tags that must retain the same vocabulary as
# the tag caption. These complement the acts sourced from the Danbooru group.
EXACT_CAPTION_TERMS |= frozenset({
    "anus",
    "bar censor",
    "breasts",
    "censored",
    "clitoris",
    "erection",
    "mosaic censoring",
    "nipples",
    "penis",
    "pussy",
    "sex toy",
})

# Danbooru uses "another" and "own" as abstract relationship placeholders.
# They should not be copied literally into natural language when the visible
# participant can be named or referred to with a pronoun.
RELATIONAL_CAPTION_TERMS = {
    "grabbing another's ass": (
        "{actor} grabbing {target_possessive} ass",
        ("grabbing", "ass"),
    ),
    "grabbing another's breast": (
        "{actor} grabbing {target_possessive} breast",
        ("grabbing", "breast"),
    ),
    "grabbing another's arm": (
        "{actor} grabbing {target_possessive} arm",
        ("grabbing", "arm"),
    ),
    "grabbing another's hair": (
        "{actor} grabbing {target_possessive} hair",
        ("grabbing", "hair"),
    ),
    "grabbing another's twintails": (
        "{actor} grabbing {target_possessive} twintails",
        ("grabbing", "twintails"),
    ),
    "holding another's hair": (
        "{actor} holding {target_possessive} hair",
        ("holding", "hair"),
    ),
    "hand on another's shoulder": (
        "{actor_possessive} hand on {target_possessive} shoulder",
        ("hand", "shoulder"),
    ),
    "grabbing own ass": (
        "{actor} grabbing {actor_possessive} own ass",
        ("grabbing", "own", "ass"),
    ),
    "grabbing own breast": (
        "{actor} grabbing {actor_possessive} own breast",
        ("grabbing", "own", "breast"),
    ),
    "hand on own ass": (
        "{actor_possessive} hand on {actor_possessive} own ass",
        ("hand", "own", "ass"),
    ),
    "hand on own thigh": (
        "{actor_possessive} hand on {actor_possessive} own thigh",
        ("hand", "own", "thigh"),
    ),
}

# Relational tags are governed by templates, never by verbatim phrase matching.
EXACT_CAPTION_TERMS -= RELATIONAL_CAPTION_TERMS.keys()


def normalize_tag(tag: str) -> str:
    return " ".join(tag.strip().lower().replace("_", " ").split())


def required_caption_terms(raw_tags: list[str]) -> list[str]:
    """Return unique exact caption terms in source-tag order."""
    terms = []
    seen = set()
    for tag in raw_tags:
        normalized = normalize_tag(tag)
        if normalized in EXACT_CAPTION_TERMS and normalized not in seen:
            terms.append(normalized)
            seen.add(normalized)
    return terms


def build_terminology_section(raw_tags: list[str]) -> str:
    """Return terminology anchors for important ground-truth act tags."""
    terms = required_caption_terms(raw_tags)
    normalized_tags = {normalize_tag(tag) for tag in raw_tags}
    relational_terms = [
        (tag, *RELATIONAL_CAPTION_TERMS[tag])
        for tag in dict.fromkeys(normalize_tag(tag) for tag in raw_tags)
        if tag in RELATIONAL_CAPTION_TERMS
    ]

    if not terms and not relational_terms:
        return ""

    fact_rules = []

    if {"sex", "vaginal"} <= normalized_tags:
        fact_rules.append(
            "Write vaginal sex as a definite visible fact. If a penis is "
            "involved, state that the penis is inserted into the pussy."
        )
    if {"sex", "anal"} <= normalized_tags:
        fact_rules.append(
            "Write anal sex as a definite visible fact. If a penis is involved, "
            "state that the penis is inserted into the anus."
        )
    if "sex from behind" in normalized_tags:
        fact_rules.append(
            "State who is having sex from behind with whom; do not merely say "
            "that one character is positioned behind another."
        )
    if "paizuri" in normalized_tags:
        fact_rules.append(
            "State paizuri as a definite act involving a penis between breasts; "
            "do not reduce it to breast stimulation."
        )
    if "mosaic censoring" in normalized_tags:
        fact_rules.append(
            "Name mosaic censoring directly. Describe the affected anatomy as "
            "pixelated rather than merely partly visible or obscured."
        )

    fact_section = ""
    if fact_rules:
        fact_section = (
            "<ground_truth_fact_rules>\n"
            + "\n".join(f"- {rule}" for rule in fact_rules)
            + "\n</ground_truth_fact_rules>\n"
        )

    exact_section = ""
    if terms:
        exact_section = (
            "# Important terminology anchors\n"
            "The phrases below name important confirmed visual concepts. "
            "Include each concept when describing the relevant subject or "
            "action, and preserve the listed phrase when it fits naturally. "
            "Minor grammatical inflection is allowed when needed for fluent "
            "prose. Never present these phrases as a list or force them into "
            "an unrelated sentence. Do not replace a precise sexual or "
            "anatomical concept with a euphemism or broader term.\n"
            "<important_terminology_anchors>\n"
            + "\n".join(terms)
            + "\n</important_terminology_anchors>\n"
        )

    relational_section = ""
    if relational_terms:
        lines = []
        for tag, template, required_words in relational_terms:
            lines.append(
                f"{tag} -> template: {template}; preserve words: "
                f"{', '.join(required_words)}"
            )
        relational_section = (
            "# Relational ground-truth concepts\n"
            "In these tags, 'another' and 'own' are relationship placeholders, "
            "not literal caption words. Resolve actor and target from the image, "
            "Torii report, and authorized character metadata. Replace each "
            "placeholder with a character name or an unambiguous possessive "
            "pronoun. Use the template grammatically and preserve its listed "
            "action/body-part words. Never write 'another's' when the target "
            "can be identified. For example, render 'grabbing another's ass' "
            "as 'a man grabbing her ass' or 'a man grabbing the woman's ass'. "
            "Never output braces, placeholder names, 'template:', or "
            "'preserve words:' in the caption.\n"
            "<relational_ground_truth_concepts>\n"
            + "\n".join(lines)
            + "\n</relational_ground_truth_concepts>\n"
        )

    return exact_section + relational_section + fact_section + "\n"
