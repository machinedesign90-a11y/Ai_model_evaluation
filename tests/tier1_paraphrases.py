"""Paraphrase test set for tier-1 retrieval (find_verified_query).

None of these are the seed's stored phrases. Each maps to the title of the draft verified query a person
would expect, or None when no verified query fits (the agent should then use tier 2).
"""

SPEAK = "Models that speak a language"
VOICE_BG = "Voiceover in a language with background audio"
API = "Models with an API"
OPEN = "Open-weights models"
LONGEST = "Longest video clips"
CHEAPEST = "Cheapest video models"
RESOLUTION = "Minimum video resolution"

PARAPHRASES: list[tuple[str, str | None]] = [
    # speak a language
    ("which video ai can talk in tamil", SPEAK),
    ("video generator that speaks spanish", SPEAK),
    ("can any video model do french speech", SPEAK),
    ("vidoe models that speek tamil", SPEAK),
    ("models with japanese voice", SPEAK),
    ("text to video with hindi audio", SPEAK),
    # voiceover + background audio
    ("hindi voiceover with background music", VOICE_BG),
    ("i need tamil narration plus background sound", VOICE_BG),
    ("video with hindi voice over and bg music", VOICE_BG),
    ("which model can add background audio to a hindi voiceover", VOICE_BG),
    ("voiceover in bengali along with ambient sound", VOICE_BG),
    # API
    ("does any video model have an api", API),
    ("video models i can call through an api", API),
    ("which image generators offer api acess", API),
    ("api available text models", API),
    # open weights
    ("open source video generators", OPEN),
    ("which video models are open weights", OPEN),
    ("video models i can self host", OPEN),
    ("opensource image model", OPEN),
    ("downloadable model weights for video", OPEN),
    # longest clips
    ("which model makes the longest videos", LONGEST),
    ("longest clip length video ai", LONGEST),
    ("what is the max video length i can generate", LONGEST),
    ("video model with longest duration", LONGEST),
    # cheapest
    ("cheapest ai video", CHEAPEST),
    ("most affordable video generation", CHEAPEST),
    ("lowest price video model", CHEAPEST),
    ("which video ai costs the least", CHEAPEST),
    ("chepest video model", CHEAPEST),
    # resolution
    ("video models that do 4k", RESOLUTION),
    ("which video models support 1080p", RESOLUTION),
    ("full hd video generation", RESOLUTION),
    ("video ai with at least 1440p", RESOLUTION),
    ("can i get uhd video output", RESOLUTION),
    # nothing fits: tier 2 or off-topic
    ("what is the weather today", None),
    ("compare testmodel alpha and testmodel beta", None),
    ("best model for writing python code", None),
    ("how many parameters does testmodel delta have", None),
]
