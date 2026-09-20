#!/usr/bin/env python3
"""Clean Danbooru general-tag strings in memory.

This is a stripped-down Python rewrite of the uploaded Java ActionHandler:
- no GUI
- no random shuffle
- no max-length prompt filling
- no quality/artist/character/copyright handling
- only the CSV "general" column is read and cleaned
- optional tag dropout configured in the CONFIG section, disabled by default
"""

from __future__ import annotations

import os
import random
from pathlib import Path
from typing import Iterable

# ---------------- CONFIG ----------------
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
IMAGE_FOLDER = os.path.join(SCRIPT_DIR, "..", "images")
IMAGE_FOLDER = os.path.abspath(IMAGE_FOLDER)

# ---------------- CONDITIONAL RULE CONFIG ----------------
# Removes generic tags when a more specific tag is present.
# Example: "shirt, blue shirt" -> "blue shirt".
ENABLE_CONDITIONAL_RULES = True

# Set True to write a log of which conditional rules fired.
# Useful when you want to verify rules like ("shirt", "blue shirt").
DEBUG_CONDITIONAL_RULES = False
CONDITIONAL_LOG_FILE = "conditional_rules.log"

# ---------------- DROPOUT CONFIG ----------------
# Disabled by default. Set DROPOUT_ENABLED = True to randomly remove some cleaned tags.
DROPOUT_ENABLED = True
DROPOUT_RATE = 0          # 0.15 = 15% chance to remove each non-protected tag
DROPOUT_SEED = None          # Use an int like 1234 for repeatable results, or None for random
DROPOUT_MIN_TAGS = 4         # Minimum number of tags to keep after dropout

# Add your own never-drop tags here. The Java priority-100 tags below are always protected too.
EXTRA_DROPOUT_PROTECT_TAGS = {
    "safe",
    "sensitive",
    "nsfw",
    "explicit",
    "newest",
    "recent",
    "mid",
    "early",
    "old",
}

# Hard-coded from Java calls like:
#   addPrompts( newOut, prompts, "TAG", 100 ... )
JAVA_100_DROPOUT_PROTECT_TAGS = ('ears',
 'horns',
 'halo',
 'scales',
 'nude',
 'disembodied',
 'tentacle',
 'pregnant',
 'facial',
 'cum in pussy',
 'cum in ass',
 'cum in mouth',
 'x-ray',
 'speculum',
 'cropped torso',
 'cropped legs',
 'cropped arms',
 'cropped head',
 'art program in frame',
 'censored',
 'masterpiece',
 'best quality',
 'high quality',
 'medium quality',
 'normal quality',
 'low quality',
 'worst quality',
 'good quality',
 'very aesthetic',
 'aesthetic',
 'displeasing',
 'very displeasing',
 'holding',
 'heterochromia',
 '1girl',
 '2girls',
 '3girls',
 '4girls',
 '5girls',
 '6+girls',
 '1boy',
 '2boys',
 '3boys',
 '4boys',
 '5boys',
 '6+boys',
 'no humans',
 'crowd',
 'multiple views',
 'traditional media',
 'watercolor (medium)',
 'monochrome',
 'lineart',
 'pixel art',
 'realistic',
 'dakimakura (medium)',
 'flstyle',
 'reference sheet',
 'anime screenshot',
 'colorful',
 'limited palette',
 'neon palette',
 'split theme',
 'greyscale',
 'muted color',
 'pastel colors',
 'sepia',
 'anime coloring',
 'flat color',
 'partially colored',
 'comic',
 'abstract',
 'gore',
 'landscape',
 'cityscape',
 'chibi',
 'loli',
 'shota',
 'onee-shota',
 'amputee',
 'futanari',
 'fairy',
 'furry',
 'goblin',
 'cyclops',
 'monster',
 'harpy',
 'lamia',
 'mermaid',
 'slime',
 'oni',
 'minigirl',
 'muscular',
 'plump',
 'giantess',
 'from',
 'chromatic aberration',
 'dark',
 'profile',
 'eyewear view',
 'close-up',
 'portrait',
 'lower body',
 'full body',
 'upper body',
 'cowboy shot',
 'dutch angle',
 'upside-down',
 'selfie',
 'pov',
 'focus',
 'cross-section',
 'letterboxed',
 'viewfinder',
 'blurry',
 'night',
 'beach',
 'doorway',
 'keyhole',
 'underwater',
 'glory wall',
 'through wall',
 'car interior',
 'train interior',
 'bus interior',
 'room',
 'kitchen',
 'on grass',
 'on bed',
 'space',
 'indoors',
 'outdoors',
 'yuri',
 'anal',
 'vaginal',
 'bestiality',
 'fisting',
 'fingering',
 'cunnilingus',
 'fellatio',
 'paizuri',
 '69',
 'masturbation',
 'handjob',
 'footjob',
 'tribadism')

REPLACE_TAGS = {'pointless censoring': 'censored', 'casual nudity': 'nude', 'functionally nude': 'nude', 'aftersex': 'after sex', 'cbt': 'torture', 'handjob gesture': 'fellatio gesture', 'small nipples': 'nipples', 'consensual tentacles': 'tentacles', 'standing missionary': 'standing sex', 'veiny penis': 'penis', 'cum on upperbody': 'cum on body', 'saliva trail': 'saliva', 'fang out': 'fang', 'crying with eyes open': 'crying', 'bikini bottom only': 'topless', 'clothed male nude female': 'nude', 'disposable cup': 'cup', 'ahe gao': 'ahegao', 'big breasts': 'large breasts', 'horsecock': 'horse penis', 'naked': 'nude', 'emotionless sex': 'expressionless', 'clothed sex': 'sex', 'solo focus': 'solo', 'holographic clothing': 'iridescent', 'although she hurriedly put on clothes (meme)': 'meme', 'Sirfy': 'sirfy'}

IGNORE_EXACT_TAGS = set(['day', 'wide oval eyes', 'bad anatomy', 'female focus', 'absurdly detailed composition', 'male focus', 'hip focus', 'dated', 'meme attire', 'highres', 'collarbone', 'hetero', 'harem', 'bad leg', 'ambiguous gender', 'androgynous', 'artist self-insert', 'bare arms', 'tight clothes', 'bare legs', 'ass visible through thighs', 'eyes visible through hair', 'clothed female nude male', 'v-shaped eyebrows', 'hair spread out', 'censored with cum', 'legs together', 'personification', 'implied after sex', 'child on child', 'vision (genshin impact)', 'feet out of frame', 'knees out of frame', 'foot out of frame', 'out of frame', 'summer', 'multicolored clothes', 'polygamy', 'borrowed character', 'closed mouth', 'blush visible through hair', 'hot', 'fine fabric emphasis', 'hair down', 'banned artist', 'flexible', 'bespectacled', 'gluteal fold', 'aunt and niece', 'father and daughter', 'mother and daughter', 'husband and wife', 'sisters', 'no coat', 'siblings', 'thigh gap', 'brothers', 'twins', 'mixed bathing', 'no tail', 'implied extra ears', 'nudist', 'straight-on', 'crossover', 'idol', '10s', 'median furrow', 'hair over shoulder', 'single hair intake', 'bisexual female', 'assertive female', 'infection monitor (arknights)', 'furrowed brow', 'eyelashes', 'linea alba', 'multiple moles', 'sexually suggestive', 'out-of-frame censoring', 'no shoes', 'contemporary', 'cameo', 'dot nose', 'eyebrows hidden by hair', 'hololive fantasy', 'dimples of venus', 'manjuu (azur lane)', 'zettai ryouiki', 'fat mons', 'matching outfits', 'eyebrows', 'matching hairstyle', 'manly', 'no testicles', 'no bra', 'no panties', 'no pants', 'no headwear', 'no nose', 'parted lips', 'faceless male', 'hair intakes', 'parted bangs', 'multitasking', 'hair tubes', 'animal ear fluf', 'consensualtentacles', 'cheating(relationship)', 'solo focus', 'female only', 'female', 'parody', 'artistic error', 'bad proportions', 'nonstop nut november', 'extra ears', 'chinese zodiac', 'genderswap(mtf)', 'sound effects', 'gameplay mechanics', 'newhalf', 'concentrating', 'perineum', 'gen 2 pokemon', 'randoseru', 'brand name imitation', 'zipper pull tab', 'sagging breasts', 'butt crack', 'name connection', 'clitoral hood', 'veiny breasts', 'impossible clothes', 'asymmetricalhair', 'knees up', 'couple', 'vision(genshin_impact)', 'serafuku', 'long legs', 'underwear only', 'hair bobbles', 'breast reduction', 'uncommon stimulation', 'too literal', 'photoshop', 'husband and wives', 'unwanted exposure', 'hair flowing over', 'strap gap', 'brother and sister', 'sleeveless', 'out of character', 'male/female', 'labia', 'no legwear', 'real world location', 'in-universe location', 'alternate hair length', 'alternate hairstyle', 'virtual youtuber', 'kidnapped', 'bad end', 'alternate breast size', 'cheating (relationship)', 'interracial', 'center opening', 'gen 1 pokemon', 'gen 3 pokemon', 'gen 4 pokemon', 'gen 5 pokemon', 'gen 6 pokemon', 'gen 7 pokemon', 'gen 8 pokemon', 'gen 9 pokemon', 'gen 10 pokemon', 'mixed-sex bathing', 'alternate costume', 'artist self-reference', 'bright pupils', 'bisexual', 'aged down', 'revealing clothes', 'sfw', 'very aesthetic', 'very displeasing', 'displeasing', 'aesthetic', '1980', '1981', '1982', '1983', '1984', '1985', '1986', '1987', '1988', '1989', '1990', '1991', '1992', '1993', '1994', '1995', '1996', '1997', '1998', '1999', '2000', '2001', '2002', '2003', '2004', '2005', '2006', '2007', '2008', '2009', '2010', '2011', '2012', '2013', '2014', '2015', '2016', '2017', '2018', '2019', '2020', '2021', '2022', '2023', '2024', '2025', '2026', '2027', '2028', '2029', '2030', '2031', '2032', '2033', '2034', '2035', '2036', '2037', '2038', '2039'])

IGNORE_CONTAINS = ['SCROREXYQ:', 'pervert', 'impossible', 'prehensile', 'adapted', 'unconventional', 'cooperative', 'year of the', 'younger', 'alternate', 'canine', 'cheating', 'matching']

COLORS = ['dark', 'gold', 'aqua', 'red', 'purple', 'white', 'grey', 'blue', 'black', 'brown', 'green', 'pink', 'yellow', 'orange', 'blonde', 'iridescent']

CONDITIONAL_RULES = [('cum on body', 'cum on breasts'), ('cum on body', 'cum on ass'), ('cum on body', 'cum on hair'), ('cum', 'cum on ass'), ('cum', 'cum on breasts'), ('cum', 'cum on hair'), ('cum', 'cum on body'), ('cum', 'cum on floor'), ('cum', 'cum in pussy'), ('cum', 'cum in mouth'), ('cum', 'cum in ass'), ('cum', 'internal cumshot'), ('sex', 'clothed sex'), ('bubble', 'air bubble'), ('signature', 'logo'), ('cross', 'latin cross'), ('cross', 'cross necklace'), ('necklace', 'cross necklace'), ('panty pull', 'pantyhose pull'), ('piano', 'playing piano'), ('guitar', 'playing guitar'), ('instrument', 'playing instrument'), ('camisole', 'see-through camisole'), ('ceiling', 'ceiling light'), ('cum', 'cum pool'), ('cum', 'cum string'), ('blush', 'nose blush'), ('pizza', 'pizza slice'), ('star (symbol)', 'star pasties'), ('sheath', 'sheathed'), ('microphone', 'holding microphone'), ('holding', 'holding microphone'), ('panties around one ankle', 'panties around one leg'), ('fur trim', 'fur-trimmed kimono'), ('cum', 'cum through clothes'), ('cum', 'cum in clothes'), ('cum', 'cumdrip'), ('apron', 'maid apron'), ('maid', 'maid apron'), ('enmaided', 'maid'), ('braid', 'twin braids'), ('fish tail', 'shark tail'), ('ahoge', 'huge ahoge'), ('braid', 'multiple braids'), ('book', 'bookshelf'), ('aiming', 'aiming at viewer'), ('piercing', 'lip piercing'), ('fake animal ears', 'animal ear headphones'), ('animal ears', 'animal ear headphones'), ('headphones', 'animal ear headphones'), ('bikini', 'bikini top lift'), ('smile', 'grin'), ('teeth', 'grin'), ('huge ass', 'ass focus'), ('elbow on knee', 'knees to chest'), ('boots', 'knee boots'), ('necklace', 'multiple necklaces'), ('cum', 'projectile cum'), ('nipple bar', 'nipple piercing'), ('holding own arm', 'hand on own arm'), ('barefoot', 'single barefoot'), ('necklace', 'bead necklace'), ('looking at viewer', 'eye contact'), ('window', 'window shade'), ('cup', 'teacup'), ('tail', 'long tail'), ('hugging tail', 'hugging own tail'), ('oral', 'anilingus'), ('oral', 'cunnilingus'), ('oral', 'fellatio'), ('oral', 'irrumatio'), ('oral', 'licking testicle'), ('oral', 'licking penis'), ('oral', 'testicle sucking'), ('licking testicle', 'testicle sucking'), ('strapless', 'strapless dress'), ('dress', 'strapless dress'), ('dress', 'china dress'), ('anus', 'dark anus'), ('labia', 'dark labia'), ('nipples', 'dark nipples'), ('sensei (blue archive)', 'doodle sensei (blue archive)'), ('parted hair', 'hair over one eye'), ('necktie', 'undone necktie'), ('pubic hair', 'sparse pubic hair'), ('wavy mouth', 'smug'), ('covered nipples', 'underboob'), ('undone neck ribbon', 'loose neck ribbon'), ('neck ribbon', 'loose neck ribbon'), ('ribbon', 'loose neck ribbon'), ('ribbon', 'neck ribbon'), ('off shoulder', 'crop top'), ('crop top', 'crop top overhang'), ('smile', 'seductive smile'), ('navel', 'midriff'), ('smile', 'evil smile'), ('smile', 'crazy smile'), ('smile', 'light smile'), ('official alternate costume', 'alternate costume'), ('hand on own arm', 'crossed arms'), ('bandages', 'bandaged leg'), ('looking away', 'looking to the side'), ('cowgirl position', 'reverse cowgirl position'), ('nude', 'bottomless'), ('nude', 'topless'), ('hat', 'unworn hat'), ('thong', 'thong bikini'), ('bikini', 'thong bikini'), ('feet', 'foot focus'), ('barefoot', 'foot focus'), ('dress', 'sweater dress'), ('sweater', 'sweater dress'), ('holding', 'holding clipboard'), ('clipboard', 'holding clipboard'), ('tight dress', 'microdress'), ('microdress', 'short dress'), ('bottle', 'perfume bottle'), ('bottle', 'glass bottle'), ('chinese clothes', 'chinese dress'), ('mermaid', 'mermaid girl'), ('clothing', 'clothed sex'), ('interlocked fingers', 'holding hands'), ('mirror', 'looking at mirror'), ('makeup', 'makeup brush'), ('highleg bikini', 'string bikini'), ('nail polish', 'toenail polish'), ('shooting star', 'star (sky)'), ('topless', 'bottomless'), ('shirt', 'wet shirt'), ('arm held back', 'arm grab'), ('presenting', 'presenting another'), ('presenting another', 'presenting pussy'), ('pussy', 'presenting pussy'), ('presenting', 'presenting pussy'), ('tearing up', 'tears'), ('bag', 'school bag'), ('dark-skinned female', 'very dark skin'), ('lamp', 'desk lamp'), ('hand on own ass', 'grabbing own ass'), ('wet', 'wet shirt'), ('holding handheld game console', 'holding'), ('petite', 'loli'), ('bra', 'bra lift'), ('pubic hair', 'pubic hair peek'), ('faceless', 'faceless female'), ('faceless', 'faceless male'), ('architecture', 'east asian architecture'), ('architecture', 'european architecture'), ('chair', 'armchair'), ('family portrait', 'portrait (object)'), ('off shoulder', 'off-shoulder sweater'), ('pointy breasts', 'perky breasts'), ('torn', 'torn pantyhose'), ('string panties', 'side-tie panties'), ('breasts', 'gigantic breasts'), ('front-tie top', 'front-tie bikini top'), ('breasts', 'huge breasts'), ('food', 'food in mouth'), ('food', 'food bite'), ('breasts', 'large breasts'), ('breasts', 'medium breasts'), ('hat', 'hat bow'), ('pectorals', 'large pectorals'), ('nipples', 'nude'), ('penis', 'penis out'), ('clock', 'wall clock'), ('skirt', 'unworn skirt'), ('ascot', 'unworn ascot'), ('tissue', 'used tissue'), ('gloves', 'paw gloves'), ('cum', 'ejaculation'), ('bow', 'hat bow'), ('navel', 'micro bikini'), ('breasts', 'small breasts'), ('plaid', 'plaid skirt'), ('plaid', 'plaid dress'), ('plaid', 'plaid vest'), ('condom', 'condom wrapper'), ('imminent penetration', 'just the tip'), ('condom', 'condom packet strip'), ('braid', 'french braid'), ('no panties', 'bottomless'), ('camera', 'holding camera'), ('socks', 'loose socks'), ('robe', 'bathrobe'), ('hood', 'hood down'), ('milk tea', 'bubble tea'), ('licking', 'licking ear'), ('clothes lift', 'shirt lift'), ('see-through', 'see-through camisole'), ('between legs', 'hand between legs'), ('gag', 'ring gag'), ('hair ornament', 'hairclip'), ('antenna hair', 'ahoge'), ('bokeh', 'depth of field'), ('scrunchie', 'hair scrunchie'), ('coin', 'gold coin'), ('paizuri', 'autopaizuri'), ('fellatio', 'autofellatio'), ('pasties', 'heart pasties'), ('pancake', 'souffle pancake'), ('curled horns', 'goat horns'), ('lollipop', 'holding lollipop'), ('pillow', 'heart-shaped pillow'), ('heart', 'heart-shaped pillow'), ('holding', 'holding lollipop'), ('holding', 'holding candy'), ('holding', 'holding shield'), ('candy', 'holding candy'), ('breathing', 'heavy breathing'), ('blurry foreground', 'depth of field'), ('breasts', 'flat chest'), ('ass', 'anus peek'), ('potato chips', 'chips (food)'), ('lace', 'lace-trimmed legwear'), ('lace', 'lace trim'), ('hat', 'porkpie hat'), ('drinking glass', 'cocktail glass'), ('ring', 'multiple rings'), ('holding', 'holding pen'), ('pen', 'holding pen'), ('pool', 'poolside'), ('panty pull', 'panties around one leg'), ('short sleeves', 'sleeves rolled up'), ('looking back', 'looking at another'), ('wet clothes', 'wet swimsuit'), ('back', 'looking back'), ('sex', 'group sex'), ('highleg swimsuit', 'one-piece swimsuit'), ('threesome', 'mmf threesome'), ('threesome', 'ffm threesome'), ('threesome', 'fff threesome'), ('threesome', 'mmm threesome'), ('breasts', 'breasts apart'), ('licking', 'licking penis'), ('penis', 'licking penis'), ('lips', 'parted lips'), ('completely nude', 'nude'), ('see-through', 'see-through cleavage'), ('cleavage', 'see-through cleavage'), ('see-through', 'see-through sleeves'), ('sleeves', 'see-through sleeves'), ('see-through', 'see-through shirt'), ('shirt', 'see-through shirt'), ('see-through', 'see-through dress'), ('dress', 'see-through dress'), ('pussy', 'spread pussy'), ('hand on own cheek', 'hand on own face'), ('pillow', 'pillow sex'), ('sex', 'pillow sex'), ('upturned eyes', 'looking up'), ('charm (object)', 'bag charm'), ('ass', 'ass support'), ('sitting', 'sitting on pillow'), ('pillow', 'sitting on pillow'), ('pillow', 'pillow grab'), ('hat ornament', 'crescent hat ornament'), ('bare shoulders', 'naked apron'), ('fellatio', 'after fellatio'), ('wall', 'stone wall'), ('braid', 'braided hair rings'), ('piercing', 'tongue piercing'), ('tongue', 'tongue piercing'), ('tongue', 'tongue tattoo'), ('tattoo', 'tongue tattoo'), ('paper', 'flying paper'), ('single braid', 'side braid'), ('motor vehicle', 'car'), ('petals', 'falling petals'), ('anus', 'spread anus'), ('ass', 'cum in ass'), ('armpit', 'armpit crease'), ('carton', 'holding carton'), ('holding', 'holding carton'), ('alcohol', 'alcohol carton'), ('carton', 'alcohol carton'), ('carton', 'milk carton'), ('ass', 'spread ass'), ('bikini', 'bikini armor'), ('armor', 'bikini armor'), ('shorts', 'shorts pull'), ('shirt', 'shirt tucked in'), ('clothes pull', 'shorts pull'), ('ass', 'ass focus'), ('dress', 'dress lift'), ('hair bun', 'single hair bun'), ('toes', 'feet'), ('fertilization', 'impregnation'), ('ovum', 'impregnation'), ('sperm cell', 'impregnation'), ('ejaculation', 'internal cumshot'), ('toes', 'barefoot'), ('soles', 'barefoot'), ('legs', 'barefoot'), ('clothes lift', 'dress lift'), ('thighs', 'thighhighs'), ('back', 'bare back'), ('thighs', 'thick thighs'), ('barcode', 'barcode tattoo'), ('tattoo', 'barcode tattoo'), ('unbuttoned', 'unbuttoned shirt'), ('shirt', 'unbuttoned shirt'), ('thighs', 'between thighs'), ('hoodie', 'open hoodie'), ('pillow', 'heart pillow'), ('clothes removed', 'partially undressed'), ('holding', 'holding clothes'), ('holding', 'holding dress'), ('holding', 'holding bottle'), ('holding', 'holding book'), ('holding', 'holding staff'), ('holding', 'holding gun'), ('holding', 'holding cup'), ('cup', 'holding cup'), ('gun', 'holding gun'), ('book', 'holding book'), ('pillow', 'frilled pillow'), ('animal print', 'cow print'), ('frilled', 'frilled pillow'), ('staff', 'holding staff'), ('bottle', 'holding bottle'), ('holding', 'holding food'), ('food', 'holding food'), ('holding', 'holding hose'), ('holding', 'holding ribbon'), ('standing', 'standing split'), ('split', 'standing split'), ('standing on one leg', 'standing split'), ('hose', 'holding hose'), ('chair', 'on chair'), ('hat', 'beret'), ('bondage', 'breast bondage'), ('breasts', 'breast bondage'), ('couch', 'on couch'), ('gag', 'wiffle gag'), ('gag', 'ball gag'), ('hugging object', 'pillow hug'), ('ass', 'ass grab'), ('bound', 'bound arms'), ('blurry background', 'blurry'), ('out-of-frame censoring', 'out of frame'), ('stomach', 'navel'), ('road', 'street'), ('shota', 'onee-shota'), ('rope', 'crotch rope'), ('crotch', 'crotch rope'), ('mole', 'mole on stomach'), ('mole', 'mole on arm'), ('mole', 'mole under mouth'), ('mole', 'mole on ass'), ('mole', 'mole on thigh'), ('ass', 'mole on ass'), ('thighs', 'mole on thigh'), ('stomach', 'mole on stomach'), ('belly', 'navel'), ('navel', 'nude'), ('toenails', 'toes'), ('skirt', 'skirt lift'), ('clothes lift', 'skirt lift'), ('hug', 'hug from behind'), ('anal', 'anal fisting'), ('fisting', 'anal fisting'), ('staff', 'mage staff'), ('mage', 'mage staff'), ('pantyhose', 'pantyhose pull'), ('necktie', 'necktie between breasts'), ('covering', 'covering crotch'), ('open clothes', 'open shirt'), ('shirt', 'open shirt'), ('camera', 'camera around neck'), ('hatsune miku', 'brazilian miku'), ('closed eyes', 'half-closed eyes'), ('dress', 'dress shirt'), ('shirt', 'dress shirt'), ('jar', 'holding jar'), ('holding', 'holding jar'), ('encore (wuthering waves)', 'cosmos (wuthering waves)'), ('encore (wuthering waves)', 'cloudy (wuthering waves)'), ('spread pussy', 'spread pussy under clothes'), ('pussy', 'spread pussy under clothes'), ('shirt', 'shirt grab'), ('shirt grab', 'shirt lift'), ('striped', 'striped thighhighs'), ('armor', 'shoulder armor'), ('striped', 'striped bikini'), ('thighhighs', 'striped thighhighs'), ('stuffed animal', 'teddy bear'), ('fingernails', 'sharp fingernails'), ('fingernails', 'long fingernails'), ('boo tao (genshin impact)', 'hu tao (genshin impact)'), ('dress', 'cocktail dress'), ('crescent', 'crescent earrings'), ('earrings', 'crescent earrings'), ('sun', 'sunset'), ('tiara', 'crown'), ('piercing', 'ear piercing'), ('desk', 'school desk'), ('breasts', 'breasts squeezed together'), ('mask', 'mask pull'), ('ass', 'huge ass'), ('stomach', 'stomach bulge'), ('sex', 'sex from behind'), ('shorts', 'short shorts'), ('tongue', 'tongue out'), ('small', 'small penis'), ('clothes lift', 'lifted by self'), ('frills', 'frilled dress'), ('dress', 'frilled dress'), ('parted lips', 'grin'), ('naranja academy school uniform', 'school uniform'), ('panties', 'string panties'), ('cleavage', 'cleavage cutout'), ('remote control', 'holding remote control'), ('holding', 'holding remote control'), ('v', 'double v'), ('downblouse', 'extended downblouse'), ('wet', 'wet towel'), ('towel', 'wet towel'), ('index finger raised', 'finger to mouth'), ('clothing cutout', 'cleavage cutout'), ('absurdly long hair', 'very long hair'), ('very long hair', 'long hair'), ('long hair', 'medium hair'), ('holding underwear', 'holding panties'), ('holding', 'holding underwear'), ('underwear', 'holding underwear'), ('holding', 'holding bra'), ('bra', 'holding bra'), ('flower', 'flower-shaped pupils'), ('sky', 'blue sky'), ('dress', 'short dress'), ('erection', 'erection under clothes'), ('swimsuit', 'one-piece swimsuit'), ('hood', 'hood up'), ('hood', 'hoodie'), ('cross', 'inverted cross'), ('tentacles', 'tentacles under clothes'), ('symbol-shaped pupils', 'flower-shaped pupils'), ('symbol-shaped pupils', 'fiery pupils'), ('symbol-shaped pupils', 'heart-shaped eyes'), ('symbol-shaped pupils', 'heart-shaped pupils'), ('symbol-shaped pupils', 'rabbit-shaped pupils'), ('symbol-shaped pupils', 'star-shaped pupils'), ('symbol-shaped pupils', 'x-shaped pupils'), ('symbol-shaped pupils', 'cross-shaped pupils'), ('symbol-shaped pupils', 'diamond-shaped pupils'), ('diamond (shape)', 'diamond-shaped pupils'), ('heart', 'heart-shaped pupils'), ('heart', 'heart hands'), ('heart', 'heart hands duo'), ('heart hands', 'heart hands duo'), ('torn clothes', 'torn dress'), ('no shoes', 'socks'), ('stomping', 'crotch stomping'), ('no shoes', 'feet'), ('horns', 'dragon horns'), ('boots', 'ankle boots'), ('nipples', 'inverted nipples'), ('nontraditional playboy bunny', 'playboy bunny'), ('twintails', 'short twintails'), ('torn clothes', 'torn panties'), ('testicle grab', 'caressing testicles'), ('looking at viewer', 'looking back'), ('breasts', 'nipples'), ('cum on upper body', 'cum on body'), ('cum on lower body', 'cum on body'), ('out of frame', 'disembodied hand'), ('red headwear', 'red had'), ('underwear', 'panties'), ('doll', 'doll hug'), ('nipples', 'cum on breasts'), ('cum on chest', 'cum on breasts'), ('arm behind back', 'arm behind head'), ('arm up', 'arm behind head'), ('stuffed toy', 'stuffed animal'), ('open clothes', 'open jacket'), ('highleg', 'highleg leotard'), ('leotard', 'highleg leotard'), ('highleg', 'highleg swimsuit'), ('swimsuit', 'highleg swimsuit'), ('highleg', 'highleg panties'), ('panties', 'highleg panties'), ('highleg', 'highleg bikini'), ('bikini', 'highleg bikini'), ('strapless', 'strapless bikini'), ('bikini', 'strapless bikini'), ('strapless', 'strapless leotard'), ('leotard', 'strapless leotard'), ('fur trim', 'fur-trimmed capelet'), ('nipples', 'nipple slip'), ('pantyhose', 'print pantyhose'), ('japanese clothes', 'kimono'), ('mole', 'mole under eye'), ('mole', 'mole on pussy'), ('gloves', 'fingerless gloves'), ('tail', 'fake tail'), ('holding', 'holding fan'), ('wings', 'demon wings'), ('holding', 'holding umbrella'), ('umbrella', 'holding umbrella'), ('umbrella', 'closed umbrella'), ('umbrella', 'oil-paper umbrella'), ('holding', 'holding weapon'), ('weapon', 'holding weapon'), ('holding weapon', 'holding sword'), ('unsheathed', 'holding sword'), ('back', 'from behind'), ('hat', 'hat feather'), ('nude', 'nude cover'), ('elf', 'dark elf'), ('leaf', 'maple leaf'), ('breast slip', 'areola slip'), ('greyscale', 'monochrome'), ('masturbation', 'male masturbation'), ('scar', 'scar across eye'), ('hairband', 'frilled hairband'), ('frills', 'frilled hairband'), ('pokephilia', 'pokemon (creature)'), ('swimsuit', 'bikini'), ('holding', 'holding syringe'), ('syringe', 'holding syringe'), ('bikini', 'string bikini'), ('bikini', 'micro bikini'), ('string bikini', 'micro bikini'), ('lying', 'on stomach'), ('sleeveless', 'sleeveless shirt'), ('shirt', 'sleeveless shirt'), ('sleeveless', 'sleeveless dress'), ('dress', 'sleeveless dress'), ('bare shoulders', 'sleeveless'), ('bare shoulders', 'detached sleeves'), ('sleeveless', 'sleeveless turtleneck'), ('turtleneck', 'sleeveless turtleneck'), ('turtleneck', 'turtleneck sweater'), ('sweater', 'turtleneck sweater'), ('skirt', 'pleated skirt'), ('dress', 'backless dress'), ('shirt', 'naked shirt'), ('wet', 'wet clothes'), ('wet', 'wet hair'), ('wet', 'wet panties'), ('panties', 'wet panties'), ('joints', 'doll joints'), ('animal ear fluff', 'animal ears'), ('horns', 'goat horns'), ('horns', 'demon horns'), ('water bottle', 'bottle'), ('hat', 'straw hat'), ('sky', 'cloudy sky'), ('panties', 'panty pull'), ('knees up', 'leg up'), ('no bra', 'nipples'), ('x-ray', 'x-ray glasses'), ('see-through', 'see-through legwear'), ('no bra', 'underboob'), ('pussy juice', 'pussy juice stain'), ('one eye covered', 'hair over one eye'), ('sweater', 'sweater lift'), ('looking down', 'looking at self'), ('coin', 'coin on string'), ('looking at self', 'looking at mirror'), ('nipples', 'huge nipples'), ('glowing', 'glowing eye'), ('glowing', 'glowing eyes'), ('glowing', 'glowing weapon'), ('glowing', 'glowing hair'), ('open clothes', 'open fly'), ('denim', 'denim shorts'), ('crack', 'cracked skin'), ('kimono', 'naked kimono'), ('lips', 'realistic'), ('nose', 'realistic'), ('armor', 'full armor'), ('skirt', 'microskirt'), ('mole', 'mole on breast'), ('breasts', 'mole on breast'), ('shirt', 'tight shirt'), ('grabbing', "grabbing another's breast"), ('breasts', "grabbing another's breast"), ('grabbing', "grabbing another's hair"), ('grabbing', "grabbing another's ass"), ('ass', "grabbing another's ass"), ('grabbing', 'arm grab'), ('grabbing', 'ass grab'), ('jacket', 'fur-trimmed jacket'), ('split-color hair', 'two-tone hair'), ('sleeves past fingers', 'sleeves past wrists'), ('lace trim', 'lace-trimmed bra'), ('lace trim', 'lace-trimmed legwear'), ('lace trim', 'lace-trimmed panties'), ('long sleeves', 'sleeves past wrists'), ('pointing', 'pointing at viewer'), ('holding', 'holding box'), ('box', 'holding box'), ('standing', 'standing on one leg'), ('gloves', 'elbow gloves'), ('animal ears', 'fake animal ears'), ('night', 'night sky'), ('tail', 'holding with tail'), ('holding', 'holding with tail'), ('bikini', 'untied bikini'), ('clothing aside', 'panties aside'), ('ribbon', 'hair ribbon'), ('ribbon', 'leg ribbon'), ('holding drink', 'holding cup'), ('drink', 'holding drink'), ('bite mark', 'bite mark on shoulder'), ('hand up', 'reaching'), ('mole', 'mole above mouth'), ('controller', 'game controller'), ('game controller', 'holding game controller'), ('holding', 'holding game controller'), ('sky', 'star (sky)'), ('masturbation', 'assisted masturbation'), ('steam', 'steam censor'), ('towel', 'towel around neck'), ('clitoris', 'exposed clitoris'), ('breath', 'heavy breathing'), ('between breasts', 'strap between breasts'), ('hoodie', 'naked hoodie'), ('hair ornament', 'snowflake hair ornament'), ('nipples', 'covered nipples'), ('striped clothes', 'striped panties'), ('underwear only', 'underwear'), ('between breasts', 'arm between breasts'), ('upper teeth only', 'teeth'), ('shoes', 'putting on shoes'), ('height difference', 'age difference'), ('size difference', 'age difference'), ('height difference', 'size difference'), ('animal ears', 'animal ear piercing'), ('sex', 'after sex'), ('although she hurriedly put on clothes (meme)', 'meme'), ('tongue out', 'fellatio'), ('x-ray', 'see-through'), ('condom', 'condom in mouth'), ('erection', 'handjob'), ('wavy mouth', 'rectangular mouth'), ('erection', 'fellatio'), ('flat ass', 'ass'), ('cum', 'cum inflation'), ('bikini top only', 'nude'), ('straddling', 'spread legs'), ('strapless bikini', 'string bikini'), ('inflation', 'cum inflation'), ('hair bun', 'cone hair bun'), ('swimsuit', 'striped bikini'), ('bare shoulders', 'striped bikini'), ('adjusting footwear', 'putting on shoes'), ('shawl', 'see-through shawl'), ("hand on another's face", "hand on another's cheek"), ('small breasts', 'flat chest'), ('male penetrated', 'ejaculating while penetrated'), ('medium breasts', 'small breasts'), ('large breasts', 'medium breasts'), ('huge breasts', 'large breasts'), ('gigantic breasts', 'huge breasts'), ('panties', 'side-tie panties'), ('bikini', 'side-tie bikini bottom'), ('blurry', 'depth of field'), ('string bikini', 'untied bikini'), ('two-tone hair', 'gradient hair'), ('tattoo', 'stomach tattoo'), ('stomach', 'stomach tattoo'), ('tattoo', 'pubic tattoo'), ('wide spread legs', 'spread legs'), ('teeth', 'sharp teeth'), ('hat', 'hair bow'), ('nipple stimulation', 'breast sucking'), ('peeing', 'peeing self'), ('pee', 'peeing'), ('firefighter', 'firefighter jacket'), ('jacket', 'firefighter jacket'), ('bow', 'hair bow'), ('gaping', 'gaping pussy'), ('pov', 'pov crotch'), ('pussy', 'gaping pussy'), ('anus', 'gaping anus'), ('gaping', 'gaping anus'), ('shirt', 'collared shirt'), ('penis', 'large penis'), ('breasts', 'one breast out'), ('penis', 'huge penis'), ('cervix', 'cross-section'), ('large penis', 'huge penis'), ('clitoris', 'erect clitoris'), ('clitoris', 'clitoris piercing'), ('penis', 'small penis'), ('penis', 'twitching penis'), ('twitching', 'twitching penis'), ('huge penis', 'large penis'), ('veiny penis', 'penis'), ('invisible', 'invisible penis'), ('invisible', 'invisible man'), ('invisible', 'invisible chair'), ('chair', 'invisible chair'), ('penis', 'invisible penis'), ('sitting', 'sitting on person'), ('sitting on person', 'girl on top'), ('veins', 'veiny penis'), ('crying with eyes open', 'crying'), ('horns', 'fake horns'), ('skirt', 'high-waist skirt'), ('breast milk', 'lactation'), ('boots', 'thigh boots'), ('penis', 'animal penis'), ('animal', 'animal penis'), ('animal penis', 'knotted penis'), ('animal penis', 'horse penis'), ('nail', 'nail polish'), ('nail', 'fingernails'), ('earrings', 'hoop earrings'), ('pillow', 'head on pillow'), ('maid', 'maid headdress'), ('ass', 'ass juice'), ('testicles', 'caressing testicles'), ('testicles', 'large testicles'), ('bottomless female', 'bottomless'), ('gloves', 'two-tone gloves'), ('bad reflection', 'reflection'), ('dark skin', 'dark-skinned male'), ('dark-skinned male', 'dark-skinned female'), ('muscular', 'muscular male'), ('muscular', 'muscular female'), ('armpit', 'licking armpit'), ('licking', 'licking armpit'), ('t-shirt', 'shirt'), ('shirt', 'print shirt'), ('dark skin', 'very dark skin'), ('coffee', 'coffee mug'), ('mug', 'coffee mug'), ('pussy', 'pussy juice'), ('pubic hair', 'female pubic hair'), ('pubic hair', 'male pubic hair'), ('male pubic hair', 'female pubic hair'), ('male pubic hair', '1girl'), ('male pubic hair', '2girls'), ('male pubic hair', '3girls'), ('male pubic hair', '4girls'), ('holding', 'holding sword'), ('sword', 'holding sword'), ('heart', 'heart on chest'), ('gore', 'candy gore'), ('candy', 'candy gore'), ('heart', 'heart earrings'), ('earrings', 'heart earrings'), ('thighs', 'spread legs'), ('hat', 'witch hat'), ('through wall', 'glory wall'), ('witch', 'witch hat'), ('tassel', 'tassel earrings'), ('bottle', 'wine bottle'), ('covering', 'covering nipples'), ('covering breasts', 'covering nipples'), ('reverse outfit', 'reverse bunnysuit'), ('navel', 'reverse bunnysuit'), ('holding', 'holding phone'), ('multicolored eyes', 'gradient eyes'), ('holding', 'holding tray'), ('sleeves', 'puffy sleeves'), ('sitting', 'sitting on lap'), ('wings', 'bat wings'), ('sitting on person', 'sitting on lap'), ('puffy sleeves', 'puffy long sleeves'), ('puffy sleeves', 'puffy short sleeves'), ('sitting', 'sitting on face'), ('sleeves', 'short sleeves'), ('short sleeves', 'puffy short sleeves'), ('long sleeves', 'puffy long sleeves'), ('tray', 'holding tray'), ('phone', 'holding phone'), ('phone', 'smartphone'), ('phone', 'cellphone'), ('heart', 'heart maebari'), ('maebari', 'heart maebari'), ('animal ears', 'animal ears (norankkori)'), ('breasts', 'breast sucking'), ('breastfeeding', 'breast sucking'), ('breast sucking', 'self breast sucking'), ('cellphone', 'smartphone'), ('thighs', 'thigh strap'), ('teeth', 'clenched teeth'), ('vibrator', 'egg vibrator'), ('condom', 'used condom'), ('horns', 'multiple horns'), ('horns', 'uneven horns'), ('breasts', 'breast lift'), ('fox girl', 'fox ears'), ('cow girl', 'cow ears'), ('twintails', 'low twintails'), ('crotch', 'pussy'), ('feet', 'feet up'), ('side-tie bikini bottom', 'string bikini'), ('bag', 'bag charm'), ('dappled sunlight', 'sunlight'), ('clothing cutout', 'back cutout'), ('hair ribbon', 'tress ribbon'), ('enmaided', 'maid headdress'), ('leotard', 'fishnet leotard'), ('fishnets', 'fishnet leotard'), ('thighhighs', 'single thighhigh'), ('gloves', 'single glove'), ('sleeves', 'single sleeve'), ('fucked silly', 'ahegao'), ('wall', 'through wall'), ('crack', 'cracked wall'), ('hat', 'tokin hat'), ('bulge', 'looking at bulge'), ('furry', 'furry male'), ('bikini', 'bikini under clothes'), ('huge testicles', 'huge penis'), ('ejaculation', 'handsfree ejaculation'), ('testicles', 'huge penis'), ('mimic', 'mimic chest'), ('chest', 'mimic chest'), ('out of frame', 'head out of frame'), ('between breasts', 'necktie between breasts'), ('bra', 'bra pull'), ('bra', 'bra strap'), ('bra', 'bra peek'), ('bra', 'bra slip'), ('bra slip', 'bra peek'), ('bra pull', 'bra lift'), ('ponytail', 'side ponytail'), ('ponytail', 'high ponytail'), ('ponytail', 'low ponytail'), ('jacket', 'track jacket'), ('swimsuit', 'string bikini'), ('swimsuit', 'competition swimsuit'), ('swimsuit', 'wet swimsuit'), ('multicolored swimsuit', 'two-tone swimsuit'), ('pantyhose', 'fishnet pantyhose'), ('fishnets', 'fishnet pantyhose'), ('bikini', 'bikini pull'), ('tree', 'palm tree'), ('rabbit girl', 'rabbit ears'), ('bikini', 'two-tone bikini'), ('lowleg', 'lowleg bikini'), ('bikini', 'lowleg bikini'), ('thong aside', 'panties aside'), ('holding game controller', 'holding controller'), ('breasts', 'breasts out'), ('nipples', 'breasts out'), ('grass', 'on grass'), ('spread pussy', 'half-spread pussy'), ('horns', 'skin-covered horns'), ('pubic hair', 'colored pubic hair'), ('braid', 'single braid'), ('furry female', 'furry'), ('covered nipples', 'areola slip'), ('towel', 'naked towel'), ('apron', 'naked apron'), ('halftone', 'halftone background'), ('oni', 'oni horns'), ('horns', 'cow horns'), ('horns', 'oni horns'), ('skirt', 'skirt around one leg'), ('legs', 'skirt around one leg'), ('masturbation', 'female masturbation'), ('masturbation', 'clothed masturbation'), ('masturbation', 'futanari masturbation'), ('futanari', 'futanari masturbation'), ('standing', 'standing sex'), ('sex', 'standing sex'), ('wariza', 'sitting'), ('open mouth', "finger in another's mouth"), ('hair ornament', 'x hair ornament'), ('hair ornament', 'coin hair ornament'), ('coin', 'coin hair ornament'), ('hair ornament', 'star hair ornament'), ('star (symbol)', 'star hair ornament'), ('hair ornament', 'ghost hair ornament'), ('ghost', 'ghost hair ornament'), ('hair ornament', 'tassel hair ornament'), ('tassel', 'tassel hair ornament'), ('hair ornament', 'heart hair ornament'), ('heart', 'heart hair ornament'), ('underboob cutout', 'underboob'), ('hair ornament', 'butterfly hair ornament'), ('butterfly', 'butterfly hair ornament'), ('hair ornament', 'frog hair ornament'), ('frog', 'frog hair ornament'), ('hair ornament', 'food-themed hair ornament'), ('food', 'food-themed hair ornament'), ('hair ornament', 'feather hair ornament'), ('feather', 'feather hair ornament'), ('hair ornament', 'snake hair ornament'), ('snake', 'snake hair ornament'), ('hair ornament', 'crescent hair ornament'), ('crescent', 'crescent hair ornament'), ('hair ornament', 'leaf hair ornament'), ('leaf', 'leaf hair ornament'), ('hair ornament', 'rabbit hair ornament'), ('rabbit', 'rabbit hair ornament'), ('hair ornament', 'anchor hair ornament'), ('anchor', 'anchor hair ornament'), ('hair ornament', 'skull hair ornament'), ('skull', 'skull hair ornament'), ('hair ornament', 'cube hair ornament'), ('cube', 'cube hair ornament'), ('hair ornament', 'cross hair ornament'), ('cross', 'cross hair ornament'), ('hair ornament', 'cat hair ornament'), ('cat', 'cat hair ornament'), ('cat ornament', 'cat hair ornament'), ('hair ornament', 'carrot hair ornament'), ('carrot', 'carrot hair ornament'), ('shorts', 'shorts aside'), ('clothing aside', 'shorts aside'), ('fingering', 'anal fingering'), ('anal', 'anal fingering'), ('fingering', 'fingering through clothes'), ('holding', 'holding instrument'), ('instrument', 'holding instrument'), ('dildo', 'dildo riding'), ('dildo', 'double dildo'), ('dildo', 'spiked dildo'), ('holding', 'holding hands'), ('anal object insertion', 'anal'), ('vaginal object insertion', 'vaginal'), ('anal object insertion', 'object insertion'), ('vaginal object insertion', 'object insertion'), ('object insertion', 'dildo'), ('pole', 'pole dancing'), ('dancing', 'pole dancing'), ('braid', 'braided ponytail'), ('ponytail', 'braided ponytail'), ('flower', 'hair flower'), ('cleavage', 'flat chest'), ('clothes pull', 'clothes lift'), ('sunglasses', 'aviator sunglasses'), ('shirt', 'shirt lift'), ('penis', 'covered penis'), ('penis', 'penis on face'), ('penis on face', 'penis over one eye'), ('penis', 'penis grab'), ('penis', 'penis size difference'), ('size difference', 'penis size difference'), ('penis', 'penis awe'), ('chastity cage', 'flat chastity cage'), ('striped', 'striped panties'), ('panties', 'striped panties'), ('testicles', 'penis'), ('braid', 'crown braid'), ('moon', 'full moon'), ('veil', 'mouth veil'), ('tail through clothes', 'tail'), ('bug', 'insect'), ('bandaid', 'bandaid on face'), ('bandaid', 'bandaid on nose'), ('bandaid', 'bandaid on nipples'), ('bandaid', 'bandaids on nipples'), ('bandaid', 'bandaid on pussy'), ('tape', 'dilation tape'), ('horse', 'horse penis'), ('penis', 'horse penis'), ('penis', 'dog penis'), ('choker', 'pendant choker'), ('licking', 'licking breast'), ('licking', 'licking nipple'), ('saliva', 'saliva trail'), ('orgasm', 'female orgasm'), ('gagged', 'gag'), ('skirt', 'miniskirt'), ('holding', 'holding pom poms'), ('pom poms', 'holding pom poms'), ('chibi', 'chibi inset'), ('sex', 'sex machine'), ('pillow', 'pillow hug'), ('sex', 'happy sex'), ('happy', 'happy sex'), ('long sleeves', 'wide sleeves'), ('long sleeves', 'detached sleeves'), ('weapon', 'weapon over shoulder'), ('genderswap', 'genderswap (mtf)'), ('blood', 'blood on breasts'), ('blood', 'blood on face'), ('harness', 'chest harness'), ('o-ring', 'o-ring bikini'), ('goggles', 'goggles on head'), ('o-ring', 'o-ring thigh strap'), ('o-ring', 'o-ring top'), ('o-ring', 'o-ring bottom'), ('thighhigh', 'single thighhigh'), ('mouse girl', 'mouse ears'), ('nipples', 'puffy nipples'), ('nipples', 'topless'), ('v legs', 'spread legs'), ('computer', 'computer tower'), ('bell', 'neck bell'), ('bell', 'jingle bell'), ('fang', 'skin fang'), ('tan', 'tanlines'), ('doorway', 'pov doorway'), ('pov', 'pov doorway'), ('towel', 'holding towel'), ('open door', 'opening door'), ('holding', 'holding towel'), ('swimsuit', 'slingshot swimsuit'), ('robe', 'hooded robe'), ('coat', 'naked coat'), ('!', '!!'), ('glass', 'against glass'), ('bow', 'bow bikini'), ('bikini', 'bow bikini'), ('nude', 'nipple slip'), ('after sex', 'clothed after sex'), ('implied after sex', 'clothed after sex'), ('after sex', 'implied after sex'), ('revealing clothes', 'areola slip'), ('revealing clothes', 'nipple slip'), ('clock', 'alarm clock'), ('earrings', 'cross earrings'), ('cross', 'cross earrings'), ('pov', 'female pov'), ('slippers', 'unworn slippers'), ('shirt', 'unworn shirt'), ('table', 'wooden table'), ('chair', 'wooden chair'), ('wiffle gag', 'ball gag'), ('tan', 'one-piece tan'), ('low-tied long hair', 'long hair'), ('shibari', 'shibari over clothes'), ('holding clothes', 'holding swimsuit'), ('thighs', 'sitting'), ('sunbeam', 'light rays'), ('cropped shirt', 'crop top'), ('abstract', 'abstract background'), ("grabbing another's breast", 'grabbing from behind'), ('from behind', 'grabbing from behind'), ('backless outfit', 'backless dress'), ('condom', 'condom on penis'), ('penis', 'condom on penis'), ('pien cat (needy girl overdose)', 'chouzetsusaikawa tenshi-chan'), ('chou ame-chan (needy girl overdose)', 'chouzetsusaikawa tenshi-chan'), ('ame-chan (needy girl overdose)', 'chouzetsusaikawa tenshi-chan'), ('semi-rimless eyewear', 'glasses'), ('ribbon', 'arm ribbon'), ('bed', 'on bed'), ('straddling', 'thigh straddling'), ('floor', 'on floor'), ('cum', 'precum'), ('belt', 'belt collar'), ('collar', 'belt collar'), ('under-rim eyewear', 'semi-rimless eyewear'), ('between thighs', 'leg between thighs'), ('swimsuit', 'holding swimsuit'), ('braid', 'braided bun'), ('fishnets', 'fishnet thighhighs'), ('between legs', 'between thighs'), ('kiss', 'french kiss'), ('wings', 'feathered wings'), ('unaligned breasts', 'bouncing breasts'), ('breasts', 'bouncing breasts'), ('arms up', 'arms behind head'), ('dildo', 'hitachi magic wand'), ('ass', 'bouncing ass'), ('sweatdrop', 'sweat'), ('loli', 'oppai loli'), ('scarf', 'torn scarf'), ('long white dress', 'sleeveless white dress'), ('suspended congress', 'reverse suspended congress'), ('tail', 'rabbit tail'), ('tail', 'cat tail'), ('tail', 'fox tail'), ('tail', 'demon tail'), ('tail', 'horse tail'), ('tail', 'dog tail'), ('tail', 'wolf tail'), ('tail', 'dragon tail'), ('tail', 'mouse tail'), ('tail', 'multiple tails'), ('tail', 'fish tail'), ('panties', 'hand in panties'), ('string panties', 'hand in panties'), ('partially visible vulva', 'hand in panties'), ('holding', 'holding can'), ('beer can', 'holding can'), ('tail', 'squirrel tail'), ('anal', 'butt plug'), ('aiming at viewer', 'pointing at viewer'), ('holding', 'holding innertube'), ('massage', 'womb massage'), ('car', 'car interior'), ('train', 'train interior'), ('bus', 'bus interior'), ('peeking', 'peeking out'), ('innertube', 'holding innertube'), ('grabbing', 'grabbing own ass'), ('panties', 'panties aside'), ('handjob', 'double handjob'), ('handjob', 'gloved handjob'), ('handjob', 'milking handjob'), ('cum on tongue', 'cum in mouth'), ('tongue', 'cum on tongue'), ('cum', 'cum on tongue'), ('cum on penis', 'cum in pussy'), ('handjob', 'reverse grip handjob'), ('pussy juice trail', 'pussy juice'), ('pussy juice trail', 'cumdrip'), ('beach', 'beach umbrella'), ('umbrella', 'beach umbrella'), ('can', 'beer can'), ('streaked hair', 'multicolored hair'), ('off shoulder', 'bare shoulders'), ('beer', 'beer can'), ('cloud', 'blue sky'), ('anal', 'after anal'), ('ponytail', 'folded ponytail'), ('hat', 'hat removed'), ('headwear removed', 'hat removed'), ('bra', 'bra removed'), ('bare shoulders', 'highleg swimsuit'), ('panties', 'frilled panties'), ('choker', 'choker removed'), ('butt crack', 'ass'), ('chain', 'chain leash'), ('hair rings', 'braided hair rings'), ('leash', 'chain leash'), ('leash', 'leash pull'), ('hair ornament', 'paw hair ornament'), ('striped clothes', 'striped bikini'), ('looking at viewer', 'looking to the side'), ('clothed masturbation', 'female masturbation'), ('excessive cum', 'cum overflow'), ('cum', 'excessive cum'), ('cum', 'cum overflow'), ('bare shoulders', 'off-shoulder sweater'), ('heart', 'spoken heart'), ('braid', 'side braid'), ('pov', 'pov hands'), ('squatting', 'squatting cowgirl position'), ('cowgirl position', 'squatting cowgirl position'), ('apron', 'waist apron'), ('spread legs', 'squatting'), ('hat', 'hat ribbon'), ('bare shoulders', 'off-shoulder bikini'), ('gold', 'gold trim'), ('thighs', 'grabbing own ass'), ('thighs', 'ass grab'), ('areolae', 'nipples'), ('white background', 'cloudy sky'), ('blue background', 'cloudy sky'), ('blue background', 'blue sky'), ('white background', 'blue sky'), ('two-tone background', 'cloudy sky'), ('two-tone background', 'blue sky'), ('ribbon', 'hat ribbon'), ('fat', 'fat man'), ('braid', 'side braids'), ('bow', 'bowtie'), ('blue sky', 'cloudy sky'), ('halterneck', 'halter dress'), ('buckle', 'belt buckle'), ('between legs', 'tail between legs'), ('tail', 'tail between legs'), ('book', 'book stack'), ('naked jacket', 'open jacket'), ('animal ear legwear', 'cat ear legwear'), ('jacket', 'open jacket'), ('legs', 'legs up'), ('anus', 'anus peek'), ('legs', 'feet'), ('bound', 'bound wrists'), ('bound', 'bound legs'), ('legs', 'bound legs'), ('paw print', 'paw print socks'), ('mouth drool', 'drooling'), ('box', 'cardboard box'), ('eyepatch', 'heart eyepatch'), ('armor', 'fur-trimmed armor'), ('boots', 'fur-trimmed boots'), ('gloves', 'fur-trimmed gloves'), ('jacked', 'fur-trimmed jacked'), ('scar', 'scar on arm'), ('scar', 'scar on chest'), ('scar', 'scar on face'), ('scar', 'scar on leg'), ('scar', 'scar on nose'), ('navel', 'navel piercing'), ('piercing', 'navel piercing'), ('piercing', 'nipple piercing'), ('piercing', 'pussy piercing'), ('pussy', 'pussy piercing'), ('multiple views', 'comic'), ('pussy', 'pussy peek'), ('tail', 'tail ornament'), ('see-through clothes', 'see-through shirt'), ('tail', 'tail piercing'), ('piercing', 'tail piercing'), ('drink can', 'beer can'), ('fingerless gloves', 'single fingerless glove'), ('bracelet', 'bead bracelet'), ('flower', 'flower tattoo'), ('tattoo', 'flower tattoo'), ('floating', 'floating clothes'), ('sitting', 'sitting on stairs'), ('stairs', 'sitting on stairs'), ('blurry', 'blurry foreground'), ('noodles', 'ramen'), ('orgasm', 'ahegao'), ('female orgasm', 'ahegao'), ('city', 'cityscape'), ('city', 'city lights'), ('underwater sex', 'partially submerged'), ('tattoo', 'arm tattoo'), ('cardigan', 'open cardigan'), ('hood', 'animal ear hood'), ('ass', 'ass cutout'), ('penis', 'disembodied penis'), ('covering', 'covering breasts'), ('breasts', 'covering breasts'), ('covering privates', 'covering breasts'), ('covering privates', 'covering crotch'), ('window', 'windowsill'), ('pussy', 'tape on pussy'), ('tape', 'tape on pussy'), ('simple background', 'gradient background'), ('teeth', 'round teeth'), ('plant', 'potted plant'), ('messy', 'messy room'), ('room', 'messy room'), ('object hug', 'pillow hug'), ('toes', 'tiptoes'), ('handjob', 'nursing handjob'), ('handjob', 'two-handed handjob'), ('ass', 'cum on ass'), ('sitting', 'sitting backwards'), ('unworn headwear', 'holding hat'), ('unworn hat', 'holding hat'), ('holding clothes', 'holding hat'), ('holding', 'holding hat'), ('hat', 'holding hat'), ('breasts', 'backboob'), ('cunnilingus gesture', 'oral invitation'), ('bare shoulders', 'lingerie'), ('remote control', 'remote control vibrator'), ('vibrator', 'remote control vibrator'), ('sex toy', 'dildo'), ('sex toy', 'egg vibrator'), ('sex toy', 'vibrator'), ('anal', 'anal beads'), ('beads', 'anal beads'), ('anal', 'anal tail'), ('tail', 'anal tail'), ('panties', 'panties around one leg'), ('holding', 'undressing'), ('futanari', 'full-package futanari'), ('monster', 'monster girl'), ('monster girl', 'dragon girl'), ('dragon', 'dragon girl'), ('mini person', 'minigirl'), ('zipper', 'unzipped'), ('zipper pull tab', 'zipper'), ('dildo', 'huge dildo'), ('vibrator', 'vibrator on clitoris'), ('clitoris', 'vibrator on clitoris'), ('areola slip', 'nipple slip'), ('panties', 'unworn panties'), ('bra', 'unworn bra'), ('rape', 'imminent rape'), ('military', 'military uniform'), ('uniform', 'military uniform'), ('painting (medium)', 'watercolor (medium)'), ('legs up', 'legs over head'), ('arm behind back', 'arms behind back'), ('arms behind back', 'arms behind head'), ('shoes', 'sandals'), ('door', 'opening door'), ('long sleeves', 'puffy sleeves'), ('teeth', 'lower teeth only'), ('fangs', 'skin fangs'), ('holding', 'holding bag'), ('bag', 'holding bag'), ('exhibitionism', 'public indecency'), ('grocery bag', 'shopping bag'), ('holding', 'holding flower'), ('door handle', 'opening door'), ('curtains', 'curtain grab'), ('bare shoulders', 'sideboob'), ('cat', 'black cat'), ('angel', 'angel wings'), ('holding', 'holding chopsticks'), ('chopsticks', 'holding chopsticks'), ('sidewalk', 'street'), ('see-through', 'see-through silhouette'), ('silhouette', 'see-through silhouette'), ('strap slip', 'strap pull'), ('transparent', 'transparent umbrella'), ('umbrella', 'transparent umbrella'), ('fishnet fabric', 'fishnet panties'), ('heart', 'heart choker'), ('fishnet fabric', 'fishnet bra'), ('thighs', 'thigh focus'), ('beach', 'beach house'), ('landscape', 'cityscape'), ('highleg bikini', 'thong bikini'), ('holding phone', 'smartphone'), ('thigh focus', 'ass focus'), ('tile floor', 'tiles'), ('tassel', 'tassel choker'), ('choker', 'tassel choker'), ('sling bikini top', 'side-tie bikini bottom'), ('shorts', 'micro shorts'), ('holding', 'holding flag'), ('flag', 'holding flag'), ('mouth mask', 'mask'), ('holding', 'holding pole'), ('pole', 'holding pole'), ('mask', 'ninja mask'), ('drinking glass', 'wine glass'), ('toothbrush', 'electric toothbrush'), ('footjob', 'two-footed footjob'), ('reaching towards viewer', 'reaching'), ('outstretched arm', 'outstretched arms'), ('outstretched arm', 'reaching'), ('outstretched arms', 'reaching'), ('sfw', 'nsfw'), ('pool', 'pool ladder'), ('ladder', 'pool ladder'), ('sideways', 'on side'), ('ball', 'beachball'), ('lace-trimmed panties', 'striped panties'), ('competition swimsuit', 'one-piece swimsuit'), ('halter dress', 'halterneck'), ('impossible bodysuit', 'bodysuit'), ('bodysuit', 'multicolored bodysuit'), ('bodysuit', 'latex bodysuit'), ('latex', 'latex bodysuit'), ('bodysuit', 'ribbed bodysuit'), ('bodysuit', 'two-tone bodysuit'), ('two-tone bodysuit', 'multicolored bodysuit'), ('bodysuit', 'hooded bodysuit'), ('bodysuit', 'open bodysuit'), ('test tube', 'holding test tube'), ('holding', 'holding test tube'), ('collar', 'detached collar'), ('collar', 'wing collar'), ('collar', 'blue sailor collar'), ('collar', 'high collar'), ('collar', 'fur collar'), ('collar', 'spiked collar'), ('collar', 'animal collar'), ('police', 'police uniform'), ('uniform', 'police uniform'), ('police', 'policewoman'), ('police', 'police hat'), ('hat', 'police hat'), ('uniform', 'school uniform'), ('vest', 'open vest'), ('vest', 'sweater vest'), ('sleeves', 'wide sleeves'), ('sleeves', 'detached sleeves'), ('cunnilingus', 'after cunnilingus'), ('cunnilingus', 'standing cunnilingus'), ('standing', 'standing cunnilingus'), ('cunnilingus', 'implied cunnilingus'), ('can', 'soda can'), ('mask', 'mask on head'), ('bird', 'bird mask'), ('mask', 'bird mask'), ('emotionless sex', 'expressionless'), ('television', 'flat screen tv'), ('heart', 'heart-shaped box'), ('chocolate', 'box of chocolates'), ('holding', 'holding envelope'), ('envelope', 'holding envelope'), ('letter', 'love letter'), ('box', 'heart-shaped box'), ('box', 'box of chocolates'), ('paper', 'holding paper'), ('holding', 'holding paper'), ('office', 'office lady'), ('o-ring', 'o-ring choker'), ('choker', 'o-ring choker'), ('herta (simulated universe avatar) (honkai: star rail)', 'the herta (honkai: star rail)'), ('herta (honkai: star rail)', 'the herta (honkai: star rail)'), ('mita (miside)', 'cool mita (miside)'), ('mita (miside)', 'tiny mita (miside)'), ('mita (miside)', 'crazy mita (miside)'), ('mita (miside)', 'kind mita (miside)'), ('mita (miside)', 'mila (miside)'), ('mita (miside)', 'sleepy mita (miside)'), ('bare shoulders', 'micro bikini'), ('bare shoulders', 'bikini'), ('bare shoulders', 'string bikini'), ('bare shoulders', 'tank top'), ('american flag bikini', 'flag print'), ('sports bra', 'two-tone sports bra'), ('shorts', 'bikini shorts'), ('bikini', 'bikini shorts'), ('bikini', 'sports bikini'), ('fur trim', 'fur-trimmed jacket'), ('pajamas', 'pajamas pull'), ('holding', 'holding helmet'), ('helmet', 'holding helmet'), ('planet', 'earth (planet)'), ('buttons', 'button gap'), ('shorts', 'denim shorts'), ('necklace', 'chain necklace'), ('bare shoulders', 'sports bra'), ('thighs', 'thighband pantyhose'), ('high-waist pantyhose', 'thighband pantyhose'), ('headwear', 'hat'), ('flower', 'flower field'), ('chair', 'office chair'), ('swivel chair', 'office chair'), ('sky', 'night sky'), ('ass support', 'ass grab'), ('hands on ass', 'ass grab'), ('ass grab', 'grabbing own ass'), ('ass support', 'grabbing own ass'), ('hands on own ass', 'grabbing own ass'), ('hand on own ass', 'hands on own ass'), ('nipple stimulation', 'nipple tweak'), ('vibrator', 'vibrator on nipple'), ('nipples', 'vibrator on nipple'), ('nipples', 'nipple clamps'), ('clamps', 'nipple clamps'), ('clitoris', 'clitoris clamp'), ('bow', 'bow (weapon)'), ('bow', 'holding bow (weapon)'), ('bow (weapon)', 'holding bow (weapon)'), ('holding', 'holding bow (weapon)'), ('clamps', 'clitoris clamp'), ('vibrator', 'vibrator cord'), ('prolapse', 'uterine prolapse'), ('prolapse', 'rectal prolapse'), ('egg', 'egg laying'), ('extreme gaping', 'gaping'), ('monochrome background', 'simple background'), ('monochrome background', 'white background'), ('monochrome background', 'grey background'), ('grin', 'evil grin'), ('yin yang', 'yin yang orb'), ('multiple girls', '1girl'), ('multiple girls', '2girls'), ('multiple boys', '2boys'), ('nipples', 'nipple piercing'), ('breast pull', 'nipple pull'), ('female pubic hair', 'pubic hair peek'), ('teeth hold', 'mouth hold'), ('mouth pull', 'mouth hold'), ('jacket', 'hooded jacket'), ('paw print', 'paw print background'), ('holding', 'holding card'), ('card', 'holding card'), ('dark-skinned male', '1girl'), ('dark-skinned male', '2girls'), ('dark-skinned male', '3girls'), ('anime coloring', 'flat color'), ('halloween', 'halloween costume'), ('christmas', 'christmas tree'), ('christmas', 'merry christmas'), ('door', 'sliding doors'), ('door', 'open door'), ('door', 'doorway')]

COLOR_RULE_SINGLE = ['garter belt', 'hoodie', 'sweater vest', 'apron', 'armband', 'scarf', 'buruma', 'tassel', 'neckerchief', 'cardigan', 'lips', 'capelet', 'tank top', 'rope', 'vest', 'feathers', 'camisole', 'mask', 'necklace', 'sweater', 'kimono', 'earrings', 'leotard', 'pantyhose', 'bikini', 'thighhighs', 'panties', 'skirt', 'jacket', 'dress', 'gloves', 'shirt', 'bow', 'necktie', 'sleeves', 'cape', 'serafuku', 'flower', 'rose', 'horns', 'ribbon', 'shorts', 'halo', 'robe', 'hairband', 'choker', 'tail', 'sports bra', 'bra', 'socks', 'boots', 'belt', 'pants', 'sailor collar', 'collar', 'coat', 'bodysuit', 'one-piece swimsuit', 'fur', 'blindfold', 'armor', 'pubic hair', 'bowtie', 'wings']

COLOR_RULE_PAIRS = [('bare shoulders', 'leotard'), ('nail polish', 'nails'), ('fingernails', 'nails'), ('sex toy', 'dildo'), ('female pubic hair', 'pubic hair'), ('simple background', 'background'), ('gradient background', 'background'), ('casual one-piece swimsuit', 'one-piece swimsuit'), ('bare shoulders', 'swimsuit'), ('bare shoulders', 'bikini'), ('hair bow', 'bow'), ('thigh strap', 'garter straps'), ('footwear', 'shoes'), ('legwear', 'thighhighs')]

COLOR_RULE_PAIRS_2 = [('bow', 'bowtie'), ('eyes', 'blindfold')]


def normalize_tag(tag: str) -> str:
    """Normalize one tag for matching/output."""
    tag = str(tag).replace("\\(", "(").replace("\\)", ")")
    tag = tag.replace("_", " ")
    tag = " ".join(tag.strip().split())
    return tag.lower()


def parse_tags(tag_string: str) -> list[str]:
    """Parse a comma-separated tag string."""
    if not tag_string:
        return []

    raw_tags = str(tag_string).replace(", ,", ",").split(",")
    tags: list[str] = []
    seen: set[str] = set()

    for raw in raw_tags:
        tag = normalize_tag(raw)
        if not tag or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)

    return tags


def dedupe_preserve_order(tags: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()

    for tag in tags:
        tag = normalize_tag(tag)
        if not tag or tag in seen:
            continue
        out.append(tag)
        seen.add(tag)

    return out


def build_conditional_rules() -> list[tuple[str, str]]:
    """
    Build rules of the form:
      remove FIRST tag if SECOND tag is present.

    Example:
      ("shirt", "blue shirt") removes "shirt" when "blue shirt" exists.
    """
    rules: list[tuple[str, str]] = []

    def add(remove_tag: str, if_present_tag: str) -> None:
        remove_tag = normalize_tag(remove_tag)
        if_present_tag = normalize_tag(if_present_tag)
        if remove_tag and if_present_tag:
            rules.append((remove_tag, if_present_tag))

    for remove_tag, if_present_tag in CONDITIONAL_RULES:
        add(remove_tag, if_present_tag)

    # Java: ignoreFirstIfSecondPresentColor(main)
    # Means: remove "shirt" if "blue shirt", "red shirt", etc. exists.
    for main in COLOR_RULE_SINGLE:
        for color in COLORS:
            add(main, f"{color} {main}")

    # Java: ignoreFirstIfSecondPresentColor(ignore, present)
    # Means: remove "bare shoulders" if "blue bikini", "red bikini", etc. exists.
    for remove_tag, present_tag in COLOR_RULE_PAIRS:
        for color in COLORS:
            add(remove_tag, f"{color} {present_tag}")

    # Java: ignoreFirstIfSecondPresentColor2(ignore, present)
    # Means both:
    #   remove ignore if color+present exists
    #   remove color+ignore if another color+present exists
    for remove_tag, present_tag in COLOR_RULE_PAIRS_2:
        for color in COLORS:
            add(remove_tag, f"{color} {present_tag}")
        for remove_color in COLORS:
            for present_color in COLORS:
                add(f"{remove_color} {remove_tag}", f"{present_color} {present_tag}")

    # Dedupe rules while preserving order.
    deduped: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for rule in rules:
        if rule not in seen:
            deduped.append(rule)
            seen.add(rule)
    return deduped


ALL_CONDITIONAL_RULES = build_conditional_rules()
IGNORE_EXACT_TAGS = {normalize_tag(tag) for tag in IGNORE_EXACT_TAGS}
IGNORE_CONTAINS = tuple(normalize_tag(tag) for tag in IGNORE_CONTAINS if normalize_tag(tag))
REPLACE_TAGS = {
    normalize_tag(old): normalize_tag(new)
    for old, new in REPLACE_TAGS.items()
    if normalize_tag(old) and normalize_tag(new)
}


def is_ignored(tag: str) -> bool:
    """Return True if a tag should be removed unconditionally."""
    tag = normalize_tag(tag)

    if tag in IGNORE_EXACT_TAGS:
        return True

    if any(fragment in tag for fragment in IGNORE_CONTAINS):
        return True

    return False


def apply_conditional_rules(tags: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """
    Apply CONDITIONAL_RULES.

    Each rule is:
      remove_tag is removed if if_present_tag is also present.

    Returns:
      (cleaned_tags, fired_rules)
    """
    tags = dedupe_preserve_order(tags)

    if not ENABLE_CONDITIONAL_RULES:
        return tags, []

    present = set(tags)
    remove: set[str] = set()
    fired_rules: list[tuple[str, str]] = []

    for remove_tag, if_present_tag in ALL_CONDITIONAL_RULES:
        if remove_tag in present and if_present_tag in present:
            remove.add(remove_tag)
            fired_rules.append((remove_tag, if_present_tag))

    cleaned = [tag for tag in tags if tag not in remove]
    return dedupe_preserve_order(cleaned), fired_rules


def clean_general_tags_with_debug(general_tags: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Clean only the general tags from one CSV cell and return conditional-rule hits."""
    tags = parse_tags(general_tags)

    # Exact replacements first.
    replaced = [REPLACE_TAGS.get(tag, tag) for tag in tags]

    # Drop exact ignore tags and substring-ignore tags.
    filtered = [tag for tag in replaced if not is_ignored(tag)]
    filtered = dedupe_preserve_order(filtered)

    return apply_conditional_rules(filtered)


def clean_general_tags(general_tags: str) -> list[str]:
    """Clean only the general tags from one CSV cell."""
    cleaned, _fired_rules = clean_general_tags_with_debug(general_tags)
    return cleaned


def write_conditional_log(csv_path: Path, fired_rules: list[tuple[str, str]]) -> None:
    """Append conditional-rule hits for one file to the debug log."""
    if not DEBUG_CONDITIONAL_RULES or not fired_rules:
        return

    log_path = Path(IMAGE_FOLDER) / CONDITIONAL_LOG_FILE

    with log_path.open("a", encoding="utf-8") as f:
        f.write(f"{csv_path.name}\n")
        for remove_tag, if_present_tag in fired_rules:
            f.write(f"  removed: {remove_tag}  because present: {if_present_tag}\n")
        f.write("\n")


def parse_comma_list(value: str) -> set[str]:
    """Parse a comma-separated CLI list into normalized tags."""
    if not value:
        return set()
    return {normalize_tag(item) for item in value.split(",") if normalize_tag(item)}


def apply_tag_dropout(
    tags: list[str],
    dropout_rate: float,
    rng: random.Random,
    min_tags: int = 1,
    protected_tags: set[str] | None = None,
) -> list[str]:
    """
    Randomly remove cleaned general tags.

    This is useful for training captions when you want slightly different
    tag subsets across repeated runs.

    - dropout_rate=0.0 keeps everything.
    - dropout_rate=0.2 means each tag has a 20% chance to be removed.
    - protected_tags are never dropped.
    - min_tags prevents accidentally returning an empty tag list.
    """
    if not tags or dropout_rate <= 0:
        return tags

    if dropout_rate >= 1:
        dropout_rate = 1.0

    min_tags = max(0, min(int(min_tags), len(tags)))
    protected_tags = protected_tags or set()

    kept: list[str] = []
    for tag in tags:
        if tag in protected_tags or rng.random() >= dropout_rate:
            kept.append(tag)

    # Restore tags, in original order, until min_tags is satisfied.
    if len(kept) < min_tags:
        kept_set = set(kept)
        for tag in tags:
            if tag not in kept_set:
                kept.append(tag)
                kept_set.add(tag)
                if len(kept) >= min_tags:
                    break

    kept_set = set(kept)
    return [tag for tag in tags if tag in kept_set]
