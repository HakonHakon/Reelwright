# director.py — Gemma-powered scene breakdown + prompt writing via Ollama

import json
import logging
import math
import re

import config
from config import OLLAMA_MODEL_CREATIVE, SCENE_MIN_SEC, SCENE_MAX_SEC, SCENE_SWEET_SPOT_SEC, SUBTITLE_SAFE_MODE, NO_DIALOGUE
from llm_client import chat as llm_chat

log = logging.getLogger(__name__)

# ── System prompts ─────────────────────────────────────────────────────────

BREAKDOWN_SYSTEM = f"""You are a film director AI planning scenes for an AI video generator that creates SYNCHRONIZED AUDIO AND VIDEO together.

FIRST, before listing any scenes, output a "characters" object that defines EVERY character in the video. Each character needs a THOROUGH physical description that will be copy-pasted into every video prompt. The video model has NO memory — it needs the full description every time.

For EACH character, describe:
- Full name or identifier
- Age range, ethnicity, build, height impression
- Face: shape, skin tone, eyes, nose, mouth, facial hair, distinguishing marks
- Hair: color, style, length, texture
- Clothing: EXACT outfit — colors, materials, fit, accessories
- Mannerisms: posture, typical gestures, how they hold themselves
- Preserve EVERY explicit physical attribute from the user's brief, especially unusual anatomy, asymmetry, scars, body proportions, handedness, props in hand, visible disabilities, or body-part-specific details
- Do NOT sanitize, simplify, normalize, or omit concrete bodily traits that the user explicitly asked for
- If it's a well-known public figure: describe their REAL, RECOGNIZABLE appearance accurately

You MUST also provide a "voices" object that defines HOW each character SOUNDS. The video model generates speech audio from text, and it needs voice anchors to produce consistent, recognizable voices. For each character describe:
- Pitch: high, medium, low, deep, bass
- Timbre: gravelly, smooth, nasal, breathy, raspy, clear, warm, sharp, booming
- Accent/dialect: specific regional accent, speech pattern, pronunciation quirks
- Speaking style: cadence, rhythm, pace, emphatic patterns, verbal tics
- Delivery: confident, hesitant, theatrical, deadpan, aggressive, gentle, sarcastic
- If it's a well-known public figure: describe their REAL, RECOGNIZABLE voice accurately

You MUST also provide a "style" string that captures the overall visual style and aesthetic of the production. This is a STYLE LOCK — a short (20-40 word) description of the rendering style, color palette, visual tone, and artistic direction that will be injected into EVERY scene prompt for consistency. Examples:
- "3D Pixar-style animation with soft, rounded character models, vibrant saturated colors, warm lighting, and expressive cartoon faces"
- "Gritty handheld documentary style, desaturated colors, natural lighting, 16mm film grain, shallow depth of field"
- "Noir-influenced cinematography with high contrast black and white, dramatic shadows, low-key lighting, and Dutch angles"
Derive this from the user's brief. If they mention a style (e.g. "Pixar", "anime", "documentary"), describe that look. If no style is mentioned, default to "Photorealistic cinematic style, natural lighting, shallow depth of field, film-quality color grading."

You MUST also provide a "story_world" string that defines the narrative engine of the film. This is a STORY LOCK — 100-180 words describing the premise, central conflict, character wants, escalation rule, running joke, cause-and-effect chain, recurring object/line, and final payoff direction. It will be used to keep scenes from feeling like disconnected sketches. For comedy, mockumentary, satire, irony, sarcasm, or slapstick, the story_world MUST define a repeatable comedy machine: serious documentary claim -> absurd visible contradiction -> character mistake that makes it worse -> clue/object carried into the next scene -> payoff/callback.

You MUST also provide a "locations" object that defines EVERY recurring location or environment in the video. This is an ENVIRONMENT BIBLE for consistency. Each location needs a THOROUGH canonical visual description that will be injected into every prompt for scenes that happen there.

For EACH location, describe:
- A stable location_id (example: "lake_dock", "classroom_a", "gym_hall")
- The environment type and geography/layout
- Architecture, walls, flooring, vegetation, water, skyline, background depth
- Signature props or set dressing that should stay consistent
- Typical color palette and material feel
- Time-of-day neutral physical details that remain true across scenes
- What makes this place visually recognizable from other locations
- 60-120 words minimum for recurring hero locations

Output format:
{{
  "characters": {{
    "character_id": "Full detailed physical description here, 50-80 words minimum..."
  }},
  "voices": {{
    "character_id": "Full voice description here — pitch, timbre, accent, cadence, delivery style, 30-50 words..."
  }},
  "style": "Visual style lock description here — rendering style, color palette, lighting approach, artistic direction, 20-40 words...",
  "story_world": "Story lock here — premise, central conflict, character wants, escalation pattern, running joke/emotional question, payoff direction, 80-160 words...",
  "locations": {{
    "location_id": "Full canonical environment description here, 60-120 words minimum..."
  }},
  "scenes": [ ... ]
}}

Each scene needs:
- scene_number
- location_id (must reference a key from the locations object whenever possible)
- description (visual action + what is heard)
- characters_in_scene (list of character_ids from the characters object above)
- dialogue (THE ACTUAL WORDS TO BE SPOKEN in quotes — see DIALOGUE RULES below)
- action_description (non-dialogue visual action: "walks to desk", "picks up coffee", "gestures broadly")
- subject_action (the visible action of the main subject in the shot, including body pose, gesture, expression, and contact with objects)
- camera_action (what the camera is doing: static, pan, zoom, handheld drift, dolly, snap zoom, etc.)
- hero_moment (ONE frozen visual instant that best represents the scene for storyboarding and for the opening beat of the video prompt)
- comic_hook (one absurdly specific, memorable, or ironic visual detail that makes the scene funny or sharply distinctive)
- pose_details (specific body positioning: hands, arms, shoulders, torso angle, leg stance, gaze direction, expression, posture)
- object_interaction (specific prop/body contact: what is held, touched, leaned on, pointed at, worn, or manipulated, and how)
- story_purpose (why this scene exists in the story: reveal, complication, decision, escalation, reversal, payoff setup)
- transition_from_previous (what concrete event, emotion, line, object, or consequence from the previous scene this scene responds to)
- new_information_or_turn (what changes by the end of this scene; avoid "nothing happens")
- character_state (what each important character wants or feels in this beat, especially if it changed from the previous scene)
- callback_or_setup (one specific detail that either pays off an earlier beat or plants a later payoff)
- dialogue_intent (what the speaker is trying to get, avoid, force, hide, test, or change with the line)
- dialogue_obstacle (the concrete pressure, misunderstanding, refusal, risk, or competing want that makes the line necessary)
- dialogue_subtext (what the speaker means or fears but does not say directly)
- dialogue_turn (what must be different after this line: decision, reveal, accusation, reversal, promise, threat, joke, or mistake)
- forbidden_smalltalk (one generic line or topic this scene must NOT use, e.g. "findest du das auch?", "ich fühle diese Spannung", "wie war dein Tag?")
- comedy_claim (for comedy/mockumentary: the serious documentary claim, rule, or expectation this scene pretends to uphold)
- comedy_contradiction (the visible absurd fact that disproves that claim)
- escalation_mistake (the concrete action, misunderstanding, or overconfident choice that makes the situation worse)
- visible_evidence_object (for mystery/investigation stories: a concrete visible clue, object, trace, gauge, pipe, stain, tool, receipt, cable, trail, or physical proof that points to the next beat)
- payoff_object_or_callback (a prop, stain, line, sound, damage, or tiny consequence that carries forward or pays off later)
- action_seconds (estimated time for physical actions NOT covered by dialogue, e.g. 2 for a quick gesture, 5 for walking across room)
- shot_type (wide, medium, close-up, POV, tracking, over-the-shoulder, etc.)
- mood
- audio_description (sound effects, ambient sounds — NO background music)
- setting_description (FULL description of the environment: room type, walls, floor, background objects, props, furniture — be thorough, this gets reused verbatim)
- lighting_description (primary light source, color temperature, shadows, atmosphere)
- continuity_notes (what must match prev scene: wardrobe state, prop state, weather, damage, positioning)
- Any explicit body-part detail from the user's brief or the scene idea is NON-NEGOTIABLE: preserve exact left/right hand usage, limb position, distinctive anatomy, body proportions, facial features, and prop contact

The system will AUTO-CALCULATE duration from your dialogue + action:
- Dialogue: counted at character-appropriate words-per-minute (fast talker ~170wpm, normal ~140wpm, slow/dramatic ~110wpm)
- Action: your action_seconds added on top of dialogue time
- You do NOT set duration_seconds — it is calculated for you

DIALOGUE RULES — THIS IS CRITICAL:
- You MUST write the ACTUAL WORDS the character says, not a summary or description
- WRONG: "Trump talks about how dishwashers don't work anymore"
- WRONG: "He complains about modern appliances in his signature style"
- RIGHT: "Let me tell you about dishwashers. They used to be fantastic, the best. You press the button, beautiful clean dishes. Now? You gotta run it three times. Three times, folks! It's a disgrace."
- Write dialogue that sounds like the character — their vocabulary, rhythm, verbal tics, catchphrases
- Make dialogue carry intention and subtext: each spoken line should react to what just happened, pursue a want, hide discomfort, dodge a truth, or push the scene forward
- Avoid generic exposition, polite filler, and obvious "as you know" lines; prefer specific, slightly imperfect, situational speech
- Every spoken line must change something: pressure, decision, reveal, refusal, misunderstanding, accusation, bargain, confession, threat, joke, or concrete new information
- Avoid repeated intimacy filler such as "findest du?", "spürst du das?", "was denkst du?", "dieser Moment", "diese Spannung", "es fühlt sich magisch an" unless the user explicitly asks for that exact pattern
- Characters should speak about what is physically happening, what they want right now, what went wrong, or what they are trying to make the other person do
- If a scene has only atmosphere and no new turn, make it a silent/action scene instead of filling it with soft smalltalk
- Each scene should have real sentences the character speaks, written out word for word
- If a scene has no dialogue (pure visual/action), set dialogue to ""

AUDIO CAPABILITIES — the video generator creates real audio from your text:
- Dialogue: the model will generate speech from your exact quoted words
- Sound effects: footsteps, door creaks, glass breaking, engine rumble, typing
- Ambient: rain, wind, crowd murmur, birds, traffic, room tone
- Voice tone: whispering, shouting, calm narration, excited
- Do NOT request background music or soundtrack — it cannot generate that cleanly

IMPORTANT: If the user requests a specific total duration (e.g. "5 minute video"), you MUST plan enough scenes so the content fills that time. For a 5-minute video, write substantial dialogue and actions across ~20+ scenes. Do NOT undershoot — write more scenes with real dialogue rather than fewer long silent ones.

There is NO limit on how many scenes you can create. Be as cinematic as you want:
- Quick 2-3 second cutaway shots for reactions, inserts, or visual emphasis
- 5-10 second beats to let moments breathe or establish atmosphere
- 15-20 second dialogue or action scenes
- Up to {SCENE_MAX_SEC} second long takes for extended sequences
Mix it up like a real film editor would. Vary shot length, camera angles, and pacing to keep the audience engaged.

MULTI-CHARACTER DIALOGUE — use cinematic shot/reverse-shot coverage:
- When two or more characters are talking, cut between close-ups of each speaker
- Show the LISTENER's reaction in close-up while the other character speaks (reaction shots)
- Use over-the-shoulder shots to establish spatial relationships
- Wide/medium establishing shots to remind the viewer where everyone is
- Each character speaking gets their own scene with only THEIR dialogue line
- Don't cram multiple characters' dialogue into one scene — split them into individual shots
Respond ONLY with a valid JSON object matching the format above. No preamble. No markdown fences."""

PROMPT_WRITER_SYSTEM = """You write prompts for LTX Video 2.3, an AI that generates VIDEO WITH SYNCHRONIZED AUDIO from a single text prompt.

EACH PROMPT IS COMPLETELY INDEPENDENT. The video model has ZERO memory between scenes. It has never seen any other prompt. Every single prompt must rebuild the ENTIRE WORLD from scratch — the character, the setting, the lighting, the props, everything — as if you are describing a photograph to a blind person who has never seen anything before.

WORLD RECONSTRUCTION — EVERY PROMPT MUST CONTAIN ALL OF THIS:

1. CHARACTER (full physical description every time):
   - Full name, age range, ethnicity, build, height impression
   - Face: shape, skin tone/texture, eyes, nose, mouth, facial hair, expression
   - Hair: color, style, length, texture
   - Clothing: EXACT outfit description — suit color, tie color/pattern, shirt, shoes, accessories
   - Body language: posture, hand position, gestures, mannerisms
   - Preserve explicit bodily traits from the source context exactly: distinctive anatomy, body shape, scars, asymmetry, handedness, facial features, prop placement, and any body-part-specific details
   - NEVER write "he remains" or "Trump continues" or "the same man" — FULLY describe them again
   - If the character was described to you, copy that description VERBATIM into every prompt

2. SETTING (full environment description every time):
   - Location type: what room/space, indoors/outdoors
   - Background: what's behind the character — walls, objects, decorations, depth
   - Props: microphone, desk, chair, glass of water — everything visible
   - Floor/surface: stage, carpet, concrete, grass
   - NEVER write "same stage" or "the comedy club" without fully describing it again

3. LIGHTING (every time):
   - Primary light source: direction, color temperature, intensity
   - Secondary/fill lights, shadows, contrast
   - Atmosphere: haze, smoke, dust particles, clean air
   - Time of day if applicable

4. CAMERA (every time):
   - Shot type: wide, medium, close-up, extreme close-up
   - Angle: eye level, low angle, high angle, Dutch angle
   - Movement: static, slow dolly, tracking, handheld
   - Lens impression: telephoto compression, wide-angle distortion

5. DIALOGUE (word for word):
   - Include ALL provided dialogue lines EXACTLY as written, in quotes
   - Embed naturally in action: 'The man leans forward and says "..."'
   - Never summarize, shorten, or paraphrase

6. VOICE/SPEECH (when characters speak):
   - Describe HOW the character speaks, not just WHAT they say
   - Include voice quality: pitch, timbre, accent, cadence, delivery
   - Example: 'says in a deep, booming voice with a New York accent, emphatic and staccato'
   - If voice anchors are provided, weave them naturally into the dialogue description
   - This is critical — the model generates speech audio and needs voice cues for recognizable delivery

7. AUDIO/SOUND:
   - Ambient sounds: room tone, crowd murmur, HVAC hum, outdoor noise
   - Sound effects tied to actions: footsteps, mic feedback, glass clink
   - Do NOT request background music or soundtrack

8. VISUAL STYLE (when a style anchor is provided):
   - Weave the style description naturally into the scene
   - Apply it to characters, environment, lighting, and color palette
   - Example: if style is "Pixar animation", describe rounded 3D character models, vibrant colors, etc.
   - The style anchor must be reflected consistently in every prompt — it is a STYLE LOCK

FORMAT:
- Use clear section headings in this order when possible: Scene, Camera, Action, Performance, Environment, Motion, Audio, Lighting, Style, Constraints
- Present tense, one continuous moment
- No quality tags ("masterpiece", "8k", "cinematic")
- 420-900 words when the scene benefits from it; longer rich scene prose is preferred over thin minimal prompts
- Start with the central dramatic or visual moment of the scene in the opening sentences
- For scenes with visible movement, write 2-5 short time-coded action beats (for example 0:00-0:03) that cover the scene duration
- Keep recurring style and location anchors concise; they support the scene but should not dominate the opening
- Treat pose details, body-part positioning, and object contact as hard visual constraints, not optional flavor
- Favor vivid, playable visual beats over generic atmospheric filler
- If the scene or brief is funny, absurd, ironic, or sarcastic, keep that comic energy alive in the visual action, timing, framing, and performance
- Rich, surprising, directorly prose is welcome as long as the output remains a usable scene prompt rather than a checklist
- Associative supporting detail, tonal abstraction, and film-literate texture are welcome if they still reinforce the exact scene
- Respond with ONLY the prompt text"""

PROMPT_WRITER_SYSTEM_SAFE = """You write prompts for LTX Video 2.3, an AI that generates VIDEO WITH SYNCHRONIZED AUDIO from a single text prompt.

EACH PROMPT IS COMPLETELY INDEPENDENT. The video model has ZERO memory between scenes. It has never seen any other prompt. Every single prompt must rebuild the ENTIRE WORLD from scratch — the character, the setting, the lighting, the props, everything.

WORLD RECONSTRUCTION — EVERY PROMPT MUST CONTAIN ALL OF THIS:

1. CHARACTER:
   - Full physical description every time
   - Clothing, facial features, body language, age impression, posture
   - Preserve explicit bodily traits from the source context exactly, including handedness, body-part placement, prop contact, unusual anatomy, scars, or asymmetry

2. SETTING:
   - Full environment description every time
   - Background objects, surfaces, props, depth, atmosphere

3. LIGHTING:
   - Light direction, quality, temperature, contrast, shadows

4. CAMERA:
   - Shot type, framing, movement, angle, lens impression

5. SPEECH AND AUDIO:
   - If the scene includes dialogue, describe it as NATURAL SPOKEN AUDIO only
   - Describe who is speaking, how they sound, emotional delivery, pacing, and the topic or intent of what they say
   - DO NOT include literal quoted dialogue unless absolutely necessary
   - Keep the spoken language exactly the same as the source dialogue or user brief
   - NEVER translate non-English speech into English
   - NEVER ask for visible words, subtitles, captions, lower thirds, lyric text, speech bubbles, or on-screen typography
   - The words should be heard as speech, not shown as text

6. VOICE/SPEECH:
   - Include pitch, timbre, accent, cadence, and delivery when characters speak

7. AUDIO/SOUND:
   - Ambient sounds and sound effects are welcome
   - Do NOT request background music

8. VISUAL STYLE:
   - Apply the style lock consistently across characters, environment, and grading

CRITICAL ANTI-CAPTION RULE:
- The frame must contain NO readable text
- No subtitles
- No captions
- No words on screen
- No typography overlays
- No lower thirds
- No burned-in dialogue text

FORMAT:
- Use clear section headings in this order when possible: Scene, Camera, Action, Performance, Environment, Motion, Audio, Lighting, Style, Constraints
- Present tense, one continuous moment
- No quality tags
- 420-900 words when the scene benefits from it; longer rich scene prose is preferred over thin minimal prompts
- Start with the central visual moment of the scene, then layer in supporting world details
- For scenes with visible movement, write 2-5 short time-coded action beats (for example 0:00-0:03) that cover the scene duration
- Keep recurring style and location anchors concise; they support the scene but should not dominate the opening
- Treat pose details, body-part positioning, and object contact as hard visual constraints, not optional flavor
- Favor vivid, playable visual beats over generic atmospheric filler
- If the scene or brief is funny, absurd, ironic, or sarcastic, keep that comic energy alive in the visual action, timing, framing, and performance
- Rich, surprising, directorly prose is welcome as long as the output remains a usable scene prompt rather than a checklist
- Associative supporting detail, tonal abstraction, and film-literate texture are welcome if they still reinforce the exact scene
- Respond with ONLY the prompt text"""

NATURAL_DIALOGUE_BREAKDOWN_APPEND = """

NATURAL DIALOGUE MODE:
- Prefer believable, conversational spoken lines over heightened monologues or punchline-heavy writing
- Default to understated delivery unless the user explicitly asked for theatrical, satirical, or exaggerated speech
- Avoid forcing catchphrases, verbal tics, slogan-like repetition, or overly polished one-liners unless they are clearly part of the brief
- Let characters sound like real people speaking in the moment: contractions, simple sentence flow, brief pauses, and mild imperfection are welcome
- Give characters a specific reason to speak in each scene: wanting something, reacting to the previous beat, covering embarrassment, testing another person, or changing the subject
- Avoid bland agreement, generic observations, and exposition that could be said by anyone
- Voice descriptions should favor natural cadence, conversational rhythm, and subtle delivery over caricature
- Humor should come more from situation, behavior, framing, and contrast than from characters sounding like they are performing at the audience
"""

NATURAL_DIALOGUE_PROMPT_APPEND = """

NATURAL DIALOGUE MODE:
- When dialogue is present, stage it as believable spoken conversation, not a speech, slogan reel, or actorly showcase
- Keep delivery cues subtle and grounded unless the brief explicitly calls for broad performance
- Favor conversational cadence, natural sentence flow, brief hesitations, and human rhythm over catchphrase stacking or theatrical emphasis
- Preserve intent and subtext: the line should feel like someone trying to get something, avoid something, or recover from the previous beat
- Let humor or irony live in behavior, timing, framing, and contrast; do not make every line sound written for applause
"""

NO_DIALOGUE_BREAKDOWN_APPEND = """

NO DIALOGUE / NO SPEECH MODE:
- This production must contain no spoken dialogue, no narration, no voice-over, no whispers, and no mouth-synced speech.
- Set every scene's dialogue field to "".
- Leave dialogue_intent, dialogue_obstacle, dialogue_subtext, dialogue_turn, forbidden_smalltalk empty unless the field is needed for silent visual subtext.
- Fill duration with visible action, reaction, blocking, camera movement, prop handling, atmosphere, and story beats instead of speech.
- audio_description may include only ambient sound and action-tied sound effects; do not describe voices, talking, breathing-as-dialogue, murmurs, or spoken language.
- If the user brief contains quoted lines or asks for words, treat them as silent story intent or visual behavior, not as speech.
- For target durations, add more silent scenes and longer action_seconds rather than adding dialogue.
"""

NO_DIALOGUE_PROMPT_APPEND = """

NO DIALOGUE / NO SPEECH MODE:
- Do not request spoken words, dialogue, narration, voice-over, whispers, murmurs, or mouth-synced speech.
- The scene should play through visible performance, action, ambient sound, and sound effects only.
- Characters may react, breathe naturally, gesture, look, hesitate, laugh silently, or move their mouths closed, but they must not speak.
- Audio may include room tone, footsteps, fabric rustle, objects, weather, vehicles, crowds as nonverbal ambience only.
"""


# ── Words-Per-Minute Profiles ──────────────────────────────────────────────

WPM_PROFILES = {
    "fast":    170,   # Fast talkers, auctioneers, excited speech
    "normal":  140,   # Average conversational pace
    "slow":    110,   # Dramatic, deliberate, elderly
    "default": 140,
}

# Known character speaking rates (add more as needed)
CHARACTER_WPM = {
    "trump":     170,  # Rapid-fire, repetitive, emphatic
    "obama":     130,  # Measured, deliberate pauses
    "morgan freeman": 110,  # Slow, deep narration
    "auctioneer": 200,
}

# Known voice anchors for recognizable public figures
# These are injected as fallbacks if the LLM doesn't generate good voice descriptions
VOICE_ANCHORS = {
    "trump": "Deep, booming male voice with a distinctive New York accent. Emphatic, staccato cadence with dramatic pauses for effect. Repetitive phrasing, superlatives ('tremendous', 'the best', 'nobody'), trailing off mid-sentence then circling back. Confident, bombastic delivery.",
    "obama": "Smooth, measured baritone with a calm, professorial delivery. Slight pause between phrases, deliberate pacing. Occasional drawn-out vowels. Warm but authoritative tone, rising inflection when making a point. Clear Midwestern-meets-coastal accent.",
    "hillary clinton": "Clear, composed alto voice with a controlled Midwestern accent. Precise articulation, measured cadence. Firm and polished delivery with occasional warmth. Steady rhythm, diplomatic tone, slight rise at the end of declarative statements.",
    "morgan freeman": "Deep, resonant bass voice with a warm Southern drawl. Slow, deliberate pacing with gravity in every word. Rich, velvety timbre. Calm, wise delivery that makes everything sound like profound narration.",
    "biden": "Gravelly, warm baritone with a distinctive Scranton, Pennsylvania working-class accent. Folksy cadence, whispered asides, sudden emphatic outbursts. Frequent use of 'look' and 'here's the deal'. Earnest, sometimes halting delivery.",
}

def _estimate_wpm(brief: str) -> int:
    """Guess speaking rate from the brief content."""
    brief_lower = brief.lower()
    for name, wpm in CHARACTER_WPM.items():
        if name in brief_lower:
            return wpm
    return WPM_PROFILES["default"]


def calc_scene_duration(scene: dict, wpm: int) -> int:
    """Calculate scene duration from dialogue word count + action time.

    Returns duration in seconds, clamped to SCENE_MIN/MAX.
    """
    # Count dialogue words
    dialogue = scene.get("dialogue", "")
    # Strip quote marks and stage directions in parentheses
    import re
    clean_dialogue = re.sub(r'\([^)]*\)', '', dialogue)
    clean_dialogue = clean_dialogue.replace('"', '').replace("'", "")
    word_count = len(clean_dialogue.split()) if clean_dialogue.strip() else 0

    # Dialogue time
    dialogue_seconds = (word_count / wpm) * 60 if word_count > 0 else 0

    # Action time (non-dialogue physical actions)
    action_seconds = scene.get("action_seconds", 2)

    # Total: dialogue + action, with a minimum beat of 1s between them
    if word_count > 0 and action_seconds > 0:
        total = dialogue_seconds + 1 + action_seconds
    elif word_count > 0:
        total = dialogue_seconds + 2  # Breathing room
    else:
        total = action_seconds + 2  # Pure action scene

    duration = int(round(total))
    duration = max(SCENE_MIN_SEC, min(SCENE_MAX_SEC, duration))
    return duration


def _dialogue_word_count(dialogue: str) -> int:
    clean_dialogue = re.sub(r'\([^)]*\)', '', dialogue or "")
    clean_dialogue = clean_dialogue.replace('"', '').replace("'", "")
    return len(clean_dialogue.split()) if clean_dialogue.strip() else 0


_SPEECH_AUDIO_MARKERS = (
    "dialogue", "spoken", "speaks", "speaking", "voice", "voices", "narration",
    "voice-over", "voiceover", "whisper", "whispering", "murmur", "murmuring",
    "talk", "talking", "says", "said", "speech", "sprache", "dialog", "stimme",
    "erzähler", "erzaehler", "flüst", "fluest", "spricht", "reden", "gespräch",
    "gespraech", "breath", "breathy", "breathing", "exhale", "exhales", "chuckle",
    "chuckles", "laugh", "laughing",
)

_NO_DIALOGUE_AUDIO_BEDS = (
    "gentle instrumental ambient music, soft outdoor room tone, distant birds, and subtle wind through leaves",
    "warm cinematic background music with a soft synth pad, quiet environmental ambience, and light action foley",
    "mellow lo-fi instrumental texture, low room tone, soft air movement, and small object sounds",
    "calm acoustic background music, distant natural ambience, soft fabric rustle, and light footsteps",
    "dreamy atmospheric score, gentle breeze, quiet room tone, and restrained action-tied foley",
    "subtle piano-and-pad background music, warm outdoor ambience, distant wind chimes, and soft movement sounds",
)


def _ambient_audio_bed(scene: dict | None = None) -> str:
    seed_text = ""
    if scene:
        seed_text = f"{scene.get('scene_number', '')}|{scene.get('description', '')}|{scene.get('mood', '')}"
    idx = sum(ord(ch) for ch in seed_text) % len(_NO_DIALOGUE_AUDIO_BEDS)
    return _NO_DIALOGUE_AUDIO_BEDS[idx]


def _silent_audio_description(audio: str, scene: dict | None = None) -> str:
    clean = re.sub(r"\s+", " ", str(audio or "")).strip()
    bed = _ambient_audio_bed(scene)
    if clean.lower().startswith("audio bed:"):
        return clean
    if not clean:
        return f"Audio bed: {bed}."
    if any(marker in clean.lower() for marker in _SPEECH_AUDIO_MARKERS):
        return f"Audio bed: {bed}."
    return f"Audio bed: {bed}; scene foley: {clean}."


def _scrub_no_dialogue_prompt(prompt: str) -> str:
    """Remove speech-triggering positive tokens from final LTX prompts in silent mode."""
    if not prompt:
        return prompt
    phrase_replacements = {
        r"\bno spoken dialogue\b": "closed-mouth nonverbal performance",
        r"\bno dialogue\b": "closed-mouth nonverbal performance",
        r"\bno speech\b": "closed-mouth facial expression",
        r"\bno narration\b": "instrumental ambient audio",
        r"\bno voice-over\b": "instrumental ambient audio",
        r"\bno voiceover\b": "instrumental ambient audio",
        r"\bno whispering\b": "soft ambient texture",
        r"\bno murmuring\b": "low ambient texture",
    }
    replacements = {
        r"\bspoken dialogue\b": "nonverbal performance",
        r"\bdialogue\b": "nonverbal performance",
        r"\bspoken words\b": "sound cues",
        r"\bspoken\b": "audible",
        r"\bspeech\b": "sound",
        r"\bspeaking\b": "reacting",
        r"\bspeaks\b": "reacts",
        r"\bspeak\b": "react",
        r"\btalking\b": "gesturing",
        r"\btalks\b": "gestures",
        r"\btalk\b": "gesture",
        r"\bnarration\b": "ambient sound",
        r"\bnarrator\b": "ambient presence",
        r"\bvoice-over\b": "ambient sound",
        r"\bvoiceover\b": "ambient sound",
        r"\bvoice\b": "presence",
        r"\bvoices\b": "ambient presences",
        r"\bwhispering\b": "soft room tone",
        r"\bwhispers\b": "soft room tone",
        r"\bwhisper\b": "soft room tone",
        r"\bmurmuring\b": "low room tone",
        r"\bmurmurs\b": "low room tone",
        r"\bmurmur\b": "low room tone",
        r"\bbreathy\b": "soft",
        r"\bbreathing\b": "subtle chest movement",
        r"\bbreathes\b": "holds posture",
        r"\bbreath\b": "micro-movement",
        r"\bchuckling\b": "closed-mouth smiling",
        r"\bchuckles\b": "smiles",
        r"\bchuckle\b": "smile",
        r"\blaughing\b": "closed-mouth smiling",
        r"\blaughs\b": "smiles",
        r"\blaugh\b": "smile",
        r"\bmouth-synced\b": "facial",
        r"\bYouTube\b": "online-video-style",
        r"\badvertisement\b": "commercial-style",
    }
    cleaned = prompt
    for pattern, replacement in phrase_replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    for pattern, replacement in replacements.items():
        cleaned = re.sub(pattern, replacement, cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned) if "\n" not in cleaned else re.sub(r"[ \t]+", " ", cleaned)
    return cleaned.strip()


def _is_tiny_nature_hook(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(
        marker in lowered
        for marker in (
            "petal", "flower petal", "ladybug", "lady bug", "moth", "bee",
            "butterfly", "insect", "bug crawls", "lands on", "crawls across",
        )
    )


def _strip_dialogue_from_scenes(scenes: list[dict], wpm: int | None = None) -> list[dict]:
    """Hard runtime guard for silent productions, regardless of what the LLM returned."""
    if not _no_dialogue_enabled():
        return scenes
    removed = 0
    for scene in scenes:
        if str(scene.get("dialogue", "") or "").strip():
            removed += 1
        scene["dialogue"] = ""
        scene["dialogue_intent"] = ""
        scene["dialogue_obstacle"] = ""
        scene["dialogue_subtext"] = ""
        scene["dialogue_turn"] = ""
        scene["forbidden_smalltalk"] = ""
        scene["audio_description"] = _silent_audio_description(scene.get("audio_description", ""), scene)
        if _is_tiny_nature_hook(scene.get("comic_hook", "")):
            scene["comic_hook"] = ""
        if _is_tiny_nature_hook(scene.get("callback_or_setup", "")):
            scene["callback_or_setup"] = ""
        if wpm is not None:
            scene["duration_seconds"] = calc_scene_duration(scene, wpm)
    if removed:
        log.info("No-dialogue mode removed spoken dialogue from %d scene(s).", removed)
    return scenes


def _renumber_scenes(scenes: list[dict], wpm: int | None = None) -> list[dict]:
    """Make scene numbers unique and sequential after LLM JSON parsing."""
    changed = False
    seen = []
    for index, scene in enumerate(scenes, start=1):
        old_number = scene.get("scene_number")
        try:
            old_int = int(old_number)
        except (TypeError, ValueError):
            old_int = None
        if old_int != index or old_int in seen:
            changed = True
        if old_int is not None:
            seen.append(old_int)
        scene["scene_number"] = index
        scene["status"] = scene.get("status", "pending")
        if wpm is not None:
            scene["duration_seconds"] = calc_scene_duration(scene, wpm)
    if changed:
        log.warning("Renumbered scenes sequentially after duplicate/missing scene numbers from LLM output.")
    return scenes


def _normalized_dialogue_key(dialogue: str) -> str:
    clean = re.sub(r"\s+", " ", str(dialogue or "").strip().lower())
    clean = clean.strip('"“”„').strip()
    clean = re.sub(r"[^\wäöüßÄÖÜ]+", " ", clean, flags=re.UNICODE)
    return re.sub(r"\s+", " ", clean).strip()


def _duplicate_dialogue_scenes(scenes: list[dict]) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for scene in scenes:
        key = _normalized_dialogue_key(scene.get("dialogue", ""))
        if len(key.split()) < 5:
            continue
        buckets.setdefault(key, []).append(scene)
    duplicates = []
    for group in buckets.values():
        if len(group) > 1:
            duplicates.extend(group[1:])
    return duplicates


def _revise_duplicate_dialogue_scenes(scenes: list[dict], brief: str, wpm: int) -> list[dict]:
    """Repair accidental copied dialogue while preserving scene action and language."""
    if _no_dialogue_enabled():
        return scenes
    candidates = _duplicate_dialogue_scenes(scenes)
    if not candidates:
        return scenes

    log.warning("Detected duplicated dialogue in %d scene(s); asking LLM for scene-specific replacements.", len(candidates))
    payload = []
    previous_by_number = {int(scene.get("scene_number") or 0): scene for scene in scenes}
    for scene in candidates:
        scene_number = int(scene.get("scene_number") or 0)
        prev_scene = previous_by_number.get(scene_number - 1, {})
        payload.append({
            "scene_number": scene_number,
            "duplicated_dialogue": scene.get("dialogue", ""),
            "description": scene.get("description", ""),
            "characters_in_scene": scene.get("characters_in_scene", []),
            "action_description": scene.get("action_description", ""),
            "subject_action": scene.get("subject_action", ""),
            "object_interaction": scene.get("object_interaction", ""),
            "story_purpose": scene.get("story_purpose", ""),
            "transition_from_previous": scene.get("transition_from_previous", ""),
            "new_information_or_turn": scene.get("new_information_or_turn", ""),
            "comedy_claim": scene.get("comedy_claim", ""),
            "comedy_contradiction": scene.get("comedy_contradiction", ""),
            "escalation_mistake": scene.get("escalation_mistake", ""),
            "visible_evidence_object": scene.get("visible_evidence_object", ""),
            "payoff_object_or_callback": scene.get("payoff_object_or_callback", ""),
            "dialogue_intent": scene.get("dialogue_intent", ""),
            "dialogue_obstacle": scene.get("dialogue_obstacle", ""),
            "dialogue_subtext": scene.get("dialogue_subtext", ""),
            "dialogue_turn": scene.get("dialogue_turn", ""),
            "previous_scene_dialogue": prev_scene.get("dialogue", ""),
            "previous_scene_turn": prev_scene.get("new_information_or_turn", ""),
        })

    rewrite_prompt = (
        "Some scenes accidentally reused the exact same dialogue. Rewrite ONLY the duplicated dialogue and dialogue engine fields.\n\n"
        "Rules:\n"
        "- Keep the same language as the user brief and original dialogue.\n"
        "- Preserve the scene action, character, and plot direction.\n"
        "- Do not reuse the duplicated line or paraphrase it closely.\n"
        "- The new line must fit THIS scene's concrete situation and must change something: accusation, denial, bad excuse, discovery, decision, joke, or mistake.\n"
        "- For comedy/mystery, mention or react to the visible evidence object when available.\n"
        "- Keep each line roughly similar in length to the original unless the scene needs a little more substance.\n"
        "- Return ONLY valid JSON: {\"scenes\": [{\"scene_number\": 1, \"dialogue_intent\": \"...\", \"dialogue_obstacle\": \"...\", \"dialogue_subtext\": \"...\", \"dialogue_turn\": \"...\", \"forbidden_smalltalk\": \"...\", \"dialogue\": \"...\"}]}\n\n"
        f"USER BRIEF:\n{brief}\n\n"
        f"SCENES WITH DUPLICATED DIALOGUE:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    response = llm_chat(
        model=OLLAMA_MODEL_CREATIVE,
        messages=[
            {"role": "system", "content": _breakdown_system_prompt()},
            {"role": "user", "content": rewrite_prompt},
        ],
        options=_breakdown_llm_options({"temperature": 0.95, "num_predict": 4096, "num_ctx": 16384}),
    )
    raw = response["message"]["content"].strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        raw = raw[start:end]

    try:
        parsed = _try_parse_json(raw)
    except Exception as exc:
        log.warning("Duplicate-dialogue rewrite parse failed, keeping original duplicate lines: %s", exc)
        return scenes

    rewrites = {}
    for item in (parsed.get("scenes", []) if isinstance(parsed, dict) else []):
        try:
            scene_number = int(item.get("scene_number"))
        except Exception:
            continue
        dialogue = str(item.get("dialogue", "") or "").strip()
        if not dialogue:
            continue
        rewrites[scene_number] = {
            key: str(item.get(key, "") or "").strip()
            for key in ("dialogue", "dialogue_intent", "dialogue_obstacle", "dialogue_subtext", "dialogue_turn", "forbidden_smalltalk")
        }

    original_duplicate_keys = {_normalized_dialogue_key(scene.get("dialogue", "")) for scene in candidates}
    for scene in scenes:
        scene_number = int(scene.get("scene_number") or 0)
        update = rewrites.get(scene_number)
        if not update or not update.get("dialogue"):
            continue
        if _normalized_dialogue_key(update["dialogue"]) in original_duplicate_keys:
            log.warning("Ignoring duplicate-dialogue rewrite for scene %d because it still matched the duplicated line.", scene_number)
            continue
        for key, value in update.items():
            if value:
                scene[key] = value
        scene["duration_seconds"] = calc_scene_duration(scene, wpm)
        log.info("Rewrote duplicated dialogue for scene %d.", scene_number)

    return scenes


def _extend_scene_plan_to_target(scenes: list[dict], target_dur: int | None, wpm: int, log_prefix: str = "") -> list[dict]:
    """Deterministic safety net when the LLM keeps undershooting requested duration."""
    if not target_dur or not scenes:
        return scenes
    total = sum(calc_scene_duration(scene, wpm) for scene in scenes)
    if total >= target_dur * 0.98:
        return scenes

    min_count, ideal_count = _target_scene_count(target_dur, _planning_target_duration(target_dur))
    target_count = max(ideal_count or 0, len(scenes) + 1)
    max_extra = max(0, (target_count * 2) - len(scenes))
    extended = [dict(scene) for scene in scenes]
    source_index = 0

    while total < target_dur * 1.02 and len(extended) < len(scenes) + max_extra:
        source = dict(scenes[source_index % len(scenes)])
        source_index += 1
        new_scene = dict(source)
        new_scene["scene_number"] = len(extended) + 1
        new_scene["status"] = "pending"
        new_scene.pop("keyframe_candidates", None)
        new_scene.pop("selected_keyframe", None)
        new_scene.pop("keyframe_approved", None)
        new_scene.pop("takes", None)
        new_scene.pop("takes_done", None)
        new_scene.pop("selected_take", None)
        new_scene.pop("ltx_prompt", None)

        base_desc = str(source.get("description", "") or "Continuation of the previous visual beat").strip()
        base_action = str(source.get("action_description", "") or source.get("subject_action", "") or "The subject continues the visible action").strip()
        new_scene["description"] = f"Continuation beat. {base_desc}"
        new_scene["action_description"] = f"Continuation of the same moment with a new micro-action: {base_action}"
        new_scene["subject_action"] = f"The subject continues the beat with a slightly different gesture, expression, or prop interaction: {source.get('subject_action') or base_action}"
        new_scene["hero_moment"] = f"A later moment in the same action: {source.get('hero_moment') or base_action}"
        new_scene["story_purpose"] = f"Duration extension that deepens the previous beat without changing location: {source.get('story_purpose', '')}"
        new_scene["transition_from_previous"] = "Direct continuation from the previous scene; preserve location, wardrobe, camera scale, and emotional state."
        new_scene["new_information_or_turn"] = source.get("new_information_or_turn") or "The same visual idea advances through a new gesture or reaction."
        new_scene["callback_or_setup"] = "" if _no_dialogue_enabled() else source.get("callback_or_setup", "")
        new_scene["comic_hook"] = "" if _no_dialogue_enabled() and _is_tiny_nature_hook(source.get("comic_hook", "")) else source.get("comic_hook", "")
        if _no_dialogue_enabled():
            new_scene["dialogue"] = ""
            new_scene["dialogue_intent"] = ""
            new_scene["dialogue_obstacle"] = ""
            new_scene["dialogue_subtext"] = ""
            new_scene["dialogue_turn"] = ""
            new_scene["forbidden_smalltalk"] = ""
            new_scene["audio_description"] = _silent_audio_description("", new_scene)
        new_scene["action_seconds"] = max(1, int(SCENE_MAX_SEC) - 2)
        new_scene["duration_seconds"] = calc_scene_duration(new_scene, wpm)
        extended.append(new_scene)
        total = sum(calc_scene_duration(scene, wpm) for scene in extended)

    for index, scene in enumerate(extended, start=1):
        scene["scene_number"] = index
        scene["duration_seconds"] = calc_scene_duration(scene, wpm)
        scene["status"] = scene.get("status", "pending")

    if len(extended) > len(scenes):
        log.info(
            "%sExtended scene plan deterministically from %d to %d scenes (~%ds) to satisfy target %ds.",
            log_prefix,
            len(scenes),
            len(extended),
            sum(scene.get("duration_seconds", 0) for scene in extended),
            target_dur,
        )
    return extended


def _needs_dialogue_expansion(scene: dict, wpm: int) -> bool:
    if _no_dialogue_enabled():
        return False
    dialogue = (scene.get("dialogue") or "").strip()
    if not dialogue:
        return False
    words = _dialogue_word_count(dialogue)
    duration = int(scene.get("duration_seconds") or calc_scene_duration(scene, wpm))
    if words <= 3:
        return True
    if duration >= 5 and words < 8:
        return True
    if duration >= 7 and words < 12:
        return True
    return False


_GENERIC_DIALOGUE_MARKERS = (
    "findest du",
    "spürst du",
    "spuerst du",
    "was denkst du",
    "wie war dein",
    "alles okay",
    "du wirkst",
    "dieser moment",
    "diesen moment",
    "diese spannung",
    "dieses gefühl",
    "dieses gefuehl",
    "es fühlt sich",
    "es fuehlt sich",
    "fast magisch",
    "wir beide",
    "nur wir",
    "hier bei uns",
    "ganz entspannt",
)

_GENERIC_TURN_MARKERS = (
    "escalate intimacy",
    "escalate tension",
    "visual escalation",
    "reveal desire",
    "offers comfort",
    "points out the atmosphere",
    "confesses her longing",
    "acknowledges the silence",
    "feels free",
    "atmospheric build",
)


def _dialogue_generic_score(scene: dict) -> int:
    dialogue = re.sub(r"\s+", " ", str(scene.get("dialogue", "") or "")).strip().lower()
    if not dialogue:
        return 0
    score = 0
    marker_hits = sum(1 for marker in _GENERIC_DIALOGUE_MARKERS if marker in dialogue)
    score += marker_hits * 2
    if dialogue.count("?") >= 3:
        score += 2
    if len(re.findall(r"\b(?:fühl\w*|fuehl\w*|moment\w*|spannung\w*|magisch\w*|atmosphäre\w*|atmosphaere\w*)\b", dialogue)) >= 3:
        score += 2
    story_text = " ".join(str(scene.get(key, "") or "").lower() for key in (
        "story_purpose", "new_information_or_turn", "dialogue_intent", "dialogue_turn"
    ))
    if any(marker in story_text for marker in _GENERIC_TURN_MARKERS):
        score += 2
    if _dialogue_word_count(dialogue) >= 35 and marker_hits >= 2:
        score += 2
    return score


def _needs_dialogue_specificity_rewrite(scene: dict) -> bool:
    if not str(scene.get("dialogue", "") or "").strip():
        return False
    return _dialogue_generic_score(scene) >= 5


def _expand_short_dialogue_scenes(scenes: list[dict], brief: str, wpm: int) -> list[dict]:
    if _no_dialogue_enabled():
        return scenes
    candidates = [scene for scene in scenes if _needs_dialogue_expansion(scene, wpm)]
    if not candidates:
        return scenes

    log.info("Expanding dialogue for %d short spoken scene(s) to avoid repeated lines in LTX.", len(candidates))

    current_payload = []
    for scene in candidates:
        current_payload.append({
            "scene_number": scene.get("scene_number"),
            "description": scene.get("description", ""),
            "mood": scene.get("mood", ""),
            "shot_type": scene.get("shot_type", ""),
            "duration_seconds": scene.get("duration_seconds"),
            "action_seconds": scene.get("action_seconds"),
            "dialogue": scene.get("dialogue", ""),
            "story_purpose": scene.get("story_purpose", ""),
            "transition_from_previous": scene.get("transition_from_previous", ""),
            "new_information_or_turn": scene.get("new_information_or_turn", ""),
            "character_state": scene.get("character_state", ""),
            "callback_or_setup": scene.get("callback_or_setup", ""),
            "visible_evidence_object": scene.get("visible_evidence_object", ""),
            "payoff_object_or_callback": scene.get("payoff_object_or_callback", ""),
            "dialogue_intent": scene.get("dialogue_intent", ""),
            "dialogue_obstacle": scene.get("dialogue_obstacle", ""),
            "dialogue_subtext": scene.get("dialogue_subtext", ""),
            "dialogue_turn": scene.get("dialogue_turn", ""),
            "forbidden_smalltalk": scene.get("forbidden_smalltalk", ""),
            "characters_in_scene": scene.get("characters_in_scene", []),
            "audio_description": scene.get("audio_description", ""),
        })

    rewrite_prompt = (
        "The following spoken scenes are too short and may cause the video model to repeat lines unnaturally.\n"
        "Rewrite ONLY the dialogue for these scenes.\n\n"
        "Rules:\n"
        "- Keep the same language as the original dialogue.\n"
        "- Keep the same meaning, tone, and character voice.\n"
        "- Expand each spoken line into fuller, natural dialogue that can fill the scene ONCE without repetition.\n"
        "- Prefer 2-4 natural sentences for narrator or monologue beats when needed.\n"
        "- Keep the lines sounding spoken, casual, and in-the-moment rather than polished for performance.\n"
        "- Use the scene's story purpose and previous-scene transition so the dialogue reacts to something concrete.\n"
        "- Avoid bland, generic lines. Each added sentence should carry pressure, subtext, a want, a dodge, a correction, or a decision.\n"
        "- Do NOT pad with repeated soft questions like 'findest du?', 'spürst du das?', 'was denkst du?', or abstract mood talk.\n"
        "- Use objects, interruptions, mistakes, decisions, refusals, or concrete new information to make the added words matter.\n"
        "- Do NOT change scene numbers.\n"
        "- Do NOT add new scenes.\n"
        "- Do NOT change action or camera details.\n"
        "- Return ONLY valid JSON in this format: {\"scenes\": [{\"scene_number\": 1, \"dialogue\": \"...\"}]}\n\n"
        f"USER BRIEF:\n{brief}\n\n"
        f"TARGET SPEAKING RATE: {wpm} WPM\n\n"
        f"SCENES TO REWRITE:\n{json.dumps(current_payload, ensure_ascii=False, indent=2)}"
    )

    response = llm_chat(
        model=OLLAMA_MODEL_CREATIVE,
        messages=[
            {"role": "system", "content": _breakdown_system_prompt()},
            {"role": "user", "content": rewrite_prompt},
        ],
        options=_breakdown_llm_options({"temperature": 0.9, "num_predict": 3072}),
    )
    raw = response["message"]["content"].strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        raw = raw[start:end]

    try:
        parsed = _try_parse_json(raw)
    except Exception as exc:
        log.warning("Short-dialogue expansion parse failed, keeping original dialogue: %s", exc)
        return scenes

    rewrites = {}
    for item in (parsed.get("scenes", []) if isinstance(parsed, dict) else []):
        try:
            scene_number = int(item.get("scene_number"))
        except Exception:
            continue
        dialogue = str(item.get("dialogue", "") or "").strip()
        if dialogue:
            rewrites[scene_number] = dialogue

    if not rewrites:
        return scenes

    for scene in scenes:
        scene_number = int(scene.get("scene_number") or 0)
        new_dialogue = rewrites.get(scene_number)
        if not new_dialogue:
            continue
        old_dialogue = scene.get("dialogue", "")
        old_words = _dialogue_word_count(old_dialogue)
        new_words = _dialogue_word_count(new_dialogue)
        if new_words <= old_words:
            continue
        scene["dialogue"] = new_dialogue
        scene["duration_seconds"] = calc_scene_duration(scene, wpm)
        log.info("Expanded scene %d dialogue from %d to %d words.", scene_number, old_words, new_words)

    return scenes


def _revise_generic_dialogue_scenes(scenes: list[dict], brief: str, wpm: int) -> list[dict]:
    if _no_dialogue_enabled():
        return scenes
    candidates = [scene for scene in scenes if _needs_dialogue_specificity_rewrite(scene)]
    if not candidates:
        return scenes

    log.info("Rewriting %d generic dialogue scene(s) for stronger intent and less smalltalk.", len(candidates))
    payload = []
    for scene in candidates:
        payload.append({
            "scene_number": scene.get("scene_number"),
            "description": scene.get("description", ""),
            "dialogue": scene.get("dialogue", ""),
            "duration_seconds": scene.get("duration_seconds"),
            "characters_in_scene": scene.get("characters_in_scene", []),
            "action_description": scene.get("action_description", ""),
            "subject_action": scene.get("subject_action", ""),
            "object_interaction": scene.get("object_interaction", ""),
            "story_purpose": scene.get("story_purpose", ""),
            "transition_from_previous": scene.get("transition_from_previous", ""),
            "new_information_or_turn": scene.get("new_information_or_turn", ""),
            "character_state": scene.get("character_state", ""),
            "callback_or_setup": scene.get("callback_or_setup", ""),
            "visible_evidence_object": scene.get("visible_evidence_object", ""),
            "payoff_object_or_callback": scene.get("payoff_object_or_callback", ""),
            "dialogue_intent": scene.get("dialogue_intent", ""),
            "dialogue_obstacle": scene.get("dialogue_obstacle", ""),
            "dialogue_subtext": scene.get("dialogue_subtext", ""),
            "dialogue_turn": scene.get("dialogue_turn", ""),
            "forbidden_smalltalk": scene.get("forbidden_smalltalk", ""),
            "generic_score": _dialogue_generic_score(scene),
        })

    rewrite_prompt = (
        "Rewrite ONLY the dialogue and dialogue engine fields for these scenes.\n\n"
        "The current dialogue is too generic or repetitive. It sounds like soft filler instead of scene drama.\n\n"
        "Rules:\n"
        "- Keep the same language as the original dialogue.\n"
        "- Preserve the character, scene action, location, and overall story direction.\n"
        "- Keep each dialogue roughly similar in length unless it is wildly overlong.\n"
        "- Every rewritten line must do at least one concrete job: pressure, refusal, accusation, reveal, bargain, dodge, decision, mistake, joke, or threat.\n"
        "- Make the speaker talk about what is physically happening, what they want right now, what went wrong, or what they are trying to make the other person do.\n"
        "- Avoid soft repeated questions and generic atmosphere: no lazy 'findest du?', 'spürst du das?', 'was denkst du?', 'dieser Moment', 'diese Spannung', 'es fühlt sich magisch an'.\n"
        "- If intimacy or emotion is needed, express it through concrete action, risk, interruption, object handling, or a specific demand.\n"
        "- Return ONLY valid JSON: {\"scenes\": [{\"scene_number\": 1, \"dialogue_intent\": \"...\", \"dialogue_obstacle\": \"...\", \"dialogue_subtext\": \"...\", \"dialogue_turn\": \"...\", \"forbidden_smalltalk\": \"...\", \"dialogue\": \"...\"}]}\n\n"
        f"USER BRIEF:\n{brief}\n\n"
        f"TARGET SPEAKING RATE: {wpm} WPM\n\n"
        f"SCENES TO REWRITE:\n{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )

    response = llm_chat(
        model=OLLAMA_MODEL_CREATIVE,
        messages=[
            {"role": "system", "content": _breakdown_system_prompt()},
            {"role": "user", "content": rewrite_prompt},
        ],
        options=_breakdown_llm_options({"temperature": 1.0, "num_predict": 6144, "num_ctx": 16384}),
    )
    raw = response["message"]["content"].strip()
    start = raw.find("{")
    end = raw.rfind("}") + 1
    if start >= 0 and end > start:
        raw = raw[start:end]

    try:
        parsed = _try_parse_json(raw)
    except Exception as exc:
        log.warning("Generic dialogue rewrite parse failed, keeping original dialogue: %s", exc)
        return scenes

    rewrites = {}
    for item in (parsed.get("scenes", []) if isinstance(parsed, dict) else []):
        try:
            scene_number = int(item.get("scene_number"))
        except Exception:
            continue
        rewrites[scene_number] = {
            key: str(item.get(key, "") or "").strip()
            for key in ("dialogue", "dialogue_intent", "dialogue_obstacle", "dialogue_subtext", "dialogue_turn", "forbidden_smalltalk")
        }

    for scene in scenes:
        scene_number = int(scene.get("scene_number") or 0)
        update = rewrites.get(scene_number)
        if not update or not update.get("dialogue"):
            continue
        old_score = _dialogue_generic_score(scene)
        old_words = _dialogue_word_count(scene.get("dialogue", ""))
        for key, value in update.items():
            if value:
                scene[key] = value
        scene["duration_seconds"] = calc_scene_duration(scene, wpm)
        log.info(
            "Rewrote scene %d dialogue specificity (score %d -> %d, %d -> %d words).",
            scene_number,
            old_score,
            _dialogue_generic_score(scene),
            old_words,
            _dialogue_word_count(scene.get("dialogue", "")),
        )

    return scenes


# ── Script Detection & Parsing ─────────────────────────────────────────────

SCRIPT_PARSE_SYSTEM = """You are a script-to-scene converter for an AI video generator that creates SYNCHRONIZED AUDIO AND VIDEO.

You will receive a screenplay, script, or structured text. Your job is to break it into individual scenes suitable for AI video generation. Each scene should be one continuous shot (5-20 seconds).

FIRST output a "characters" object describing EVERY character's full physical appearance. This is critical because the video model generates each scene independently with NO memory. Each character needs: name, age, ethnicity, build, face details, hair, EXACT clothing, accessories, mannerisms. For well-known public figures describe their real recognizable appearance accurately. 50-80 words minimum per character.
If the script or user context specifies distinctive bodily traits, handedness, scars, asymmetry, props in hand, body-part positions, or unusual physical details, preserve them explicitly and do NOT normalize them away.

ALSO output a "voices" object describing HOW each character SOUNDS. The video model generates speech audio and needs voice anchors for consistent, recognizable voices. For each character describe: pitch (high/medium/low/deep), timbre (gravelly/smooth/nasal/breathy/raspy/warm/booming), accent/dialect, speaking style and cadence, verbal tics, delivery style. For well-known public figures describe their real recognizable voice accurately. 30-50 words per character.

ALSO output a "style" string — a 20-40 word visual style lock describing the rendering style, color palette, lighting approach, and artistic direction. Derive from the script's genre/tone. This gets injected into every scene prompt for visual consistency.

ALSO output a "story_world" string — a 100-180 word story lock describing premise, central conflict, character wants, running joke or emotional question, escalation rule, cause-and-effect chain, recurring object/line, and payoff direction. For comedy/mockumentary, define the repeatable comedy machine: serious claim -> visible contradiction -> character mistake -> carried clue/object -> payoff/callback. This keeps the scene list narratively coherent.

ALSO output a "locations" object — a canonical environment bible for every recurring place in the script. Each location key should be a stable location_id and each value should be a thorough 60-120 word description of the place's layout, architecture, terrain, props, background depth, materials, and recognizable recurring visual anchors.

Output format:
{"characters": {"character_id": "full physical description..."}, "voices": {"character_id": "full voice description..."}, "style": "visual style description...", "story_world": "story lock...", "locations": {"location_id": "full canonical environment description..."}, "scenes": [...]}

For each scene output:
- scene_number
- location_id
- characters_in_scene (list of character_ids)
- description (visual action — what the camera sees)
- dialogue (the EXACT lines spoken, word for word from the script, in quotes. If no dialogue, use "")
- action_description (physical actions: "walks to desk", "picks up phone")
- subject_action (what the main visible subject is physically doing in the frame, including pose and expression)
- camera_action (what the camera is doing)
- hero_moment (one frozen visual instant that best captures the scene)
- comic_hook (one absurdly specific, memorable, or ironic visual detail that makes the shot distinctive)
- pose_details (specific body positioning: hands, arms, shoulders, torso angle, leg stance, gaze direction, expression, posture)
- object_interaction (specific prop/body contact: what is held, touched, leaned on, pointed at, worn, or manipulated, and how)
- story_purpose
- transition_from_previous
- new_information_or_turn
- character_state
- callback_or_setup
- dialogue_intent
- dialogue_obstacle
- dialogue_subtext
- dialogue_turn
- forbidden_smalltalk
- comedy_claim
- comedy_contradiction
- escalation_mistake
- visible_evidence_object
- payoff_object_or_callback
- action_seconds (time for physical actions not covered by dialogue)
- shot_type (wide, medium, close-up, POV, tracking, over-the-shoulder — choose what fits the moment)
- mood (emotional tone of the moment)
- audio_description (sound effects, ambient sounds — NO background music)
- setting_description (FULL description of the environment: room, walls, floor, props, furniture, background)
- lighting_description (light sources, color temperature, shadows, atmosphere)
- continuity_notes (what must match prev scene)

RULES:
- Preserve ALL dialogue from the script — do NOT summarize, skip, or paraphrase any lines
- Preserve ALL explicit physical attributes and body-part-specific instructions — do NOT generalize them into vague action summaries
- Split long monologues across multiple scenes with different camera angles
- One continuous action per scene — no time jumps within a scene
- If the script has stage directions, convert them to description + action_description
- If the script specifies camera angles or shots, use them
- setting_description and lighting_description must be THOROUGH — they get copied into every video prompt

Respond ONLY with valid JSON. No preamble. No markdown fences."""


def _is_script(text: str) -> bool:
    """Detect if the input looks like a screenplay/script rather than a brief."""
    import re
    lines = text.strip().split("\n")
    if len(lines) < 5:
        return False

    script_indicators = 0

    # Scene headings: INT. / EXT. / INTERIOR / EXTERIOR
    if re.search(r'(?:INT\.|EXT\.|INTERIOR|EXTERIOR)\s', text, re.IGNORECASE):
        script_indicators += 3

    # Character names in ALL CAPS followed by dialogue (standard screenplay format)
    # Pattern: line that's all caps, followed by a line of dialogue
    caps_lines = sum(1 for line in lines if line.strip().isupper() and 2 <= len(line.strip().split()) <= 5)
    if caps_lines >= 2:
        script_indicators += 2

    # Parenthetical stage directions: (beat), (smiling), (to camera)
    parens = len(re.findall(r'\([^)]{2,30}\)', text))
    if parens >= 2:
        script_indicators += 1

    # Dialogue patterns: lines in quotes with character attribution
    quoted_lines = len(re.findall(r'"[^"]{10,}"', text))
    if quoted_lines >= 3:
        script_indicators += 1

    # FADE IN, CUT TO, FADE OUT
    if re.search(r'(?:FADE IN|CUT TO|FADE OUT|DISSOLVE TO|SMASH CUT)', text, re.IGNORECASE):
        script_indicators += 2

    # Scene/Act markers
    if re.search(r'(?:SCENE \d|ACT \d|Scene:)', text, re.IGNORECASE):
        script_indicators += 2

    return script_indicators >= 3


def _chat_with_auto_tokens(model: str, messages: list, base_options: dict,
                           start_tokens: int = 12288, max_tokens: int = 49152) -> str:
    """Call ollama.chat with automatic token scaling on truncation.

    Detects truncated JSON output (unbalanced brackets) and retries with
    doubled token limit until it fits or hits max_tokens.
    """
    num_predict = start_tokens
    while num_predict <= max_tokens:
        opts = {**base_options, "num_predict": num_predict, "num_ctx": max(num_predict, 49152)}
        log.info("  LLM call: %s, num_predict=%d", model, num_predict)

        response = llm_chat(model=model, messages=messages, options=opts)
        raw = response["message"]["content"].strip()

        if not raw:
            log.warning("  Empty response, retrying with more tokens...")
            num_predict *= 2
            continue

        # Check if output looks truncated (unbalanced brackets)
        open_braces = raw.count('{') - raw.count('}')
        open_brackets = raw.count('[') - raw.count(']')

        if open_braces > 0 or open_brackets > 0:
            log.warning("  Output truncated (unbalanced: %d braces, %d brackets, %d chars). "
                        "Retrying with %d tokens...",
                        open_braces, open_brackets, len(raw), num_predict * 2)
            num_predict *= 2
            continue

        return raw

    # Hit max — return what we have, let the bracket fixer in _fix_json handle it
    log.warning("  Hit max tokens (%d), returning potentially truncated output", max_tokens)
    return raw


def parse_script(script_text: str) -> list[dict]:
    """Parse a screenplay/script into scenes using Gemma."""
    global _last_planning_debug
    log.info("Detected script format — parsing into scenes...")
    wpm = _estimate_wpm(script_text)
    log.info("Estimated speaking rate: %d WPM", wpm)
    request_prompt = f"Parse this script into scenes:\n\n{script_text}"
    if _natural_dialogue_enabled():
        request_prompt += (
            "\n\nNatural dialogue mode is enabled. Preserve the script's wording, "
            "but keep any voice characterization grounded, conversational, and understated."
        )
    if _no_dialogue_enabled():
        request_prompt += (
            "\n\nNo-dialogue mode is enabled. Convert the script into silent visual scenes only: "
            "set every dialogue field to an empty string and transform spoken intent into visible action, "
            "gesture, reaction, blocking, prop handling, and ambient/action sound."
        )

    raw = _chat_with_auto_tokens(
        model=OLLAMA_MODEL_CREATIVE,
        messages=[
            {"role": "system", "content": _script_parse_system_prompt()},
            {"role": "user", "content": request_prompt},
        ],
        base_options=_breakdown_llm_options({"temperature": 0.9}),
    )
    _last_planning_debug = {
        "mode": "script_parse",
        "system_prompt": _script_parse_system_prompt(),
        "request_prompt": request_prompt,
        "raw_response": raw,
    }
    scenes = _strip_dialogue_from_scenes(_ensure_story_continuity_fields(_parse_json(raw, retries=2, brief=script_text)))

    # Calculate durations from dialogue + action
    for s in scenes:
        s["duration_seconds"] = calc_scene_duration(s, wpm)
        s["status"] = "pending"

        dialogue = s.get("dialogue", "")
        word_count = len(dialogue.replace('"', '').split()) if dialogue else 0
        action_sec = s.get("action_seconds", 0)
        log.info("  Scene %d: %d words dialogue (%.1fs @ %dwpm) + %ds action = %ds",
                 s["scene_number"], word_count,
                 (word_count / wpm * 60) if word_count else 0,
                 wpm, action_sec, s["duration_seconds"])

    total_dur = sum(s["duration_seconds"] for s in scenes)
    log.info("Script parsed: %d scenes, total ~%ds (%.1f min)", len(scenes), total_dur, total_dur / 60)
    return scenes


# ── Scene Breakdown ────────────────────────────────────────────────────────

def _extract_target_duration(brief: str) -> int | None:
    """Extract target duration in seconds from user brief, if specified."""
    import re
    # Match patterns like "5 minute", "10 min", "3-minute", "120 seconds", "2 hour"
    m = re.search(r'(\d+)\s*[-]?\s*(minute|min|second|sec|hour|hr)s?', brief, re.IGNORECASE)
    if not m:
        return None
    val = int(m.group(1))
    unit = m.group(2).lower()
    if unit in ("hour", "hr"):
        return val * 3600
    elif unit in ("minute", "min"):
        return val * 60
    else:
        return val


def _planning_target_duration(target_dur: int | None) -> int | None:
    """Bias the first breakdown pass slightly long to avoid expensive undershoot rewrites."""
    if not target_dur:
        return None
    multiplier = 1.35 if _no_dialogue_enabled() else 1.25
    if target_dur >= 120:
        multiplier += 0.15
    if target_dur >= 300:
        multiplier += 0.20
    return max(target_dur, int(round(target_dur * multiplier)))


def _target_scene_count(target_dur: int | None, planning_target: int | None = None) -> tuple[int | None, int | None]:
    """Return a realistic minimum/ideal scene count for the clamped scene duration model."""
    if not target_dur:
        return None, None
    effective_target = planning_target or target_dur
    max_len = max(1, int(SCENE_MAX_SEC))
    min_count = max(1, math.ceil(effective_target / max_len))
    if _no_dialogue_enabled():
        ideal_len = max(4, min(max_len - 1 if max_len > 5 else max_len, int(round(max_len * 0.82))))
    else:
        ideal_len = max(5, min(max_len - 1 if max_len > 8 else max_len, int(round(SCENE_SWEET_SPOT_SEC * 0.8))))
    ideal_count = max(min_count, math.ceil(effective_target / max(1, ideal_len)))
    return min_count, ideal_count


def get_composition_lock(brief: str = "") -> str:
    """Extract hard framing/pose constraints from the user's brief."""
    clean = re.sub(r"\s+", " ", (brief or "")).strip()
    lowered = clean.lower()
    if not clean:
        return ""

    has_exact_frame = any(
        phrase in lowered for phrase in (
            "exact framing", "existing composition", "input image", "same composition",
            "same framing", "preserve the framing", "preserve framing", "no change in subject scale",
        )
    )
    has_pov = "pov" in lowered or "point of view" in lowered
    has_seated_lock = any(phrase in lowered for phrase in ("designer-sessel", "designer chair", "sitzt", "sitting", "seated"))
    has_crossed_legs = any(phrase in lowered for phrase in ("beine sind überschlagen", "legs crossed", "crossed legs"))
    has_no_close = any(phrase in lowered for phrase in ("no close-up", "no close up", "no closeups", "keine close"))
    has_no_zoom = any(phrase in lowered for phrase in ("no zoom", "no zoom-out", "without zoom", "kein zoom", "keinen zoom"))
    has_no_reveal = any(phrase in lowered for phrase in ("no reveal", "unseen areas", "no reveal of unseen"))
    has_visible_heels = "highheel" in lowered or "high heel" in lowered or "high-heel" in lowered
    optional_footwear = bool(get_optional_footwear_guidance(brief))

    if not any((has_exact_frame, has_pov, has_seated_lock, has_crossed_legs, has_no_close, has_no_zoom, has_no_reveal)):
        return ""

    parts = []
    locked_framing = has_exact_frame or has_no_zoom or has_no_close or has_no_reveal
    if has_exact_frame:
        parts.append("Preserve the original input-image framing and subject scale; all motion stays inside the existing composition.")
    if has_pov:
        if locked_framing:
            parts.append("Keep a stable POV viewer angle looking at the subject.")
        else:
            parts.append("Keep a POV viewer angle looking at the subject.")
    if has_no_zoom:
        parts.append("Do not zoom, dolly, crop tighter, or pull back.")
    if has_no_close:
        parts.append("Keep the subject in the original medium/seated composition rather than a close-up.")
    if has_no_reveal:
        parts.append("Do not reveal unseen areas beyond the established frame.")
    if has_seated_lock:
        parts.append("The subject remains seated in the designer chair on the wooden deck.")
    if has_crossed_legs:
        parts.append("Her legs remain crossed except for tiny seated adjustments that return to the crossed-leg pose.")
    if has_visible_heels and not optional_footwear:
        parts.append("Matte-black high heels remain part of the visual continuity when the lower body is visible.")

    return " ".join(dict.fromkeys(parts))


def get_optional_footwear_guidance(brief: str = "") -> str:
    """Return guidance when footwear was described as optional or low-priority."""
    lowered = re.sub(r"\s+", " ", (brief or "").lower())
    if not lowered:
        return ""
    footwear_terms = (
        "sneaker", "sneakers", "shoe", "shoes", "boot", "boots", "heel",
        "heels", "highheel", "highheels", "high-heel", "pumps", "sandals",
        "turnschuhe", "schuhe", "stiefel",
    )
    optional_terms = (
        "sometimes", "occasionally", "optional", "unimportant", "not important",
        "not dominant", "low priority", "secondary", "if visible", "only if visible",
        "manchmal", "gelegentlich", "optional", "unwichtig", "nicht wichtig",
        "nicht dominant", "nebensächlich", "falls sichtbar", "wenn sichtbar",
    )
    if any(term in lowered for term in footwear_terms) and any(term in lowered for term in optional_terms):
        return (
            "Low-priority lower-frame detail: keep it consistent only when it is already naturally visible; "
            "do not change camera distance, pose, or composition just to reveal it."
        )
    return ""


def _append_unique_anchor(value: str, addition: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    addition = re.sub(r"\s+", " ", str(addition or "")).strip()
    if not addition:
        return value
    if addition.lower() in value.lower():
        return value
    if not value or value.lower() == "none":
        return addition
    return f"{value} {addition}"


def _apply_composition_lock_to_scenes(scenes: list[dict], brief: str) -> list[dict]:
    lock = get_composition_lock(brief)
    if not lock:
        return scenes

    lowered_lock = lock.lower()
    strict_frame = "original input-image framing" in lowered_lock or "original medium/seated composition" in lowered_lock
    stable_pov = "stable pov" in lowered_lock

    for scene in scenes:
        shot_type = str(scene.get("shot_type") or "").strip()
        shot_lower = shot_type.lower()
        if strict_frame or stable_pov:
            scene["shot_type"] = "stable POV shot with original medium/seated framing" if stable_pov else "original medium/seated framing"
        elif "close" in shot_lower and "close-up" in lowered_lock:
            scene["shot_type"] = "medium shot"

        camera_lock = "Preserve the POV viewer angle."
        if strict_frame or stable_pov:
            camera_lock = "Stable camera; preserve original framing, subject scale, and POV angle."
        if "do not zoom" in lowered_lock:
            camera_lock += " No zoom, dolly, crop-in, pull-back, or reveal."
        scene["camera_action"] = _append_unique_anchor(scene.get("camera_action", ""), camera_lock)
        scene["continuity_notes"] = _append_unique_anchor(scene.get("continuity_notes", ""), f"Composition lock: {lock}")

        if "designer chair" in lowered_lock or "legs remain crossed" in lowered_lock:
            pose_lock = "Subject remains seated in the designer chair"
            if "legs remain crossed" in lowered_lock:
                pose_lock += ", legs crossed as the baseline pose"
            pose_lock += "."
            scene["pose_details"] = _append_unique_anchor(scene.get("pose_details", ""), pose_lock)

        if "high heels" in lowered_lock or "designer chair" in lowered_lock:
            object_lock = "Body stays in contact with the designer chair"
            if "high heels" in lowered_lock:
                object_lock += "; matte-black high heels remain visually consistent when visible"
            object_lock += "."
            scene["object_interaction"] = _append_unique_anchor(scene.get("object_interaction", ""), object_lock)

    return scenes


def breakdown(brief: str, force_script: bool = False) -> list[dict]:
    """Take a user brief or script and return a list of scene dicts.

    Auto-detects if the input is a screenplay/script and parses accordingly.
    Set force_script=True to skip detection and treat input as a script.
    """
    global _last_planning_debug
    # Auto-detect script vs brief
    if force_script or _is_script(brief):
        return parse_script(brief)

    log.info("Breaking down brief into scenes...")
    wpm = _estimate_wpm(brief)
    log.info("Estimated speaking rate: %d WPM", wpm)
    target_dur = _extract_target_duration(brief)
    planning_target = _planning_target_duration(target_dur)
    min_scene_count, ideal_scene_count = _target_scene_count(target_dur, planning_target)
    if target_dur:
        log.info("Target duration from brief: %ds (%.1f min)", target_dur, target_dur / 60)
    if planning_target and planning_target != target_dur:
        log.info("Planning with slight overshoot buffer: %ds (%.1f min)", planning_target, planning_target / 60)
    if min_scene_count and ideal_scene_count:
        log.info("Duration model requires at least %d scenes; asking for ~%d scenes.", min_scene_count, ideal_scene_count)

    # Phase 1: Deep planning pass — let the model think hard
    log.info("Phase 1: Deep planning (high token output, thinking mode)...")
    no_dialogue_planning_line = (
        'NO DIALOGUE / NO SPEECH MODE IS ACTIVE: set every dialogue field to "". '
        "Fill runtime with silent visual action, reaction, camera movement, props, atmosphere, and action-tied sound effects. "
        "Do not create narration, voice-over, whispers, murmurs, or spoken lines, even if the user prompt mentions words."
        if _no_dialogue_enabled() else ""
    )
    first_pass_duration_line = (
        "For the FIRST draft, aim slightly long at about " + str(planning_target) + " seconds (" + f"{planning_target/60:.1f}" + " minutes). It is better to overshoot a little than undershoot and need a full rewrite. Count your scenes and their silent action_seconds carefully."
        if _no_dialogue_enabled() and planning_target and target_dur else
        "For the FIRST draft, aim slightly long at about " + str(planning_target) + " seconds (" + f"{planning_target/60:.1f}" + " minutes). It is better to overshoot a little than undershoot and need a full rewrite. Count your scenes and their dialogue carefully."
        if planning_target and target_dur else ""
    )
    composition_lock = get_composition_lock(brief)
    composition_lock_line = (
        "COMPOSITION / POSE LOCK FROM USER BRIEF:\n"
        f"{composition_lock}\n"
        "This overrides generic cinematic variety. Keep every scene inside this stable composition; vary expression, tiny gesture, dialogue, light, and prop contact instead of camera distance or subject scale.\n"
        if composition_lock else ""
    )
    camera_planning_step = (
        "7. Because a composition lock is active, do NOT vary camera angles for visual variety. Keep the same locked POV/framing in every scene; create variety through small seated gestures, expressions, dialogue turns, lighting changes, and prop contact."
        if composition_lock else
        "7. Plan camera angles for visual variety — don't repeat the same shot type consecutively."
    )
    duration_planning_step = (
        f"3. How many scenes do you need? The system clamps every scene to {SCENE_MAX_SEC}s maximum, so a {target_dur}s target needs at least {min_scene_count or 'many'} scenes and should usually use about {ideal_scene_count or 'many'} scenes. Calculate every scene as silent action_seconds plus 2 seconds, clamped to {SCENE_MIN_SEC}-{SCENE_MAX_SEC}s. Sum all scene durations — does it hit the planning target of {planning_target or target_dur or 'the requested duration'}s?"
        if _no_dialogue_enabled() else
        f"3. How many scenes do you need? The system clamps every scene to {SCENE_MAX_SEC}s maximum, so a {target_dur}s target needs at least {min_scene_count or 'many'} scenes and should usually use about {ideal_scene_count or 'many'} scenes. For each scene with dialogue, count the words and estimate seconds at {wpm} WPM; for action-only scenes, estimate action time. Sum all scene durations — does it hit the planning target of {planning_target or target_dur or 'the requested duration'}s?"
    )
    dialogue_planning_steps = (
        """5. Set every dialogue field to "" and keep speech/narration/voice-over completely absent.
5.5. Do NOT use placeholder dialogue, breaths-as-dialogue, whispers, murmurs, or offscreen talking to fill time.
6. Give each silent scene a purpose through behavior: reaction, request shown physically, refusal, concealment, correction, confession-by-action, joke-under-pressure, or decision.
6.1. Leave dialogue engine fields empty unless you use them as silent visual subtext.
6.2. Ban soft filler loops. Do not replace dialogue with generic staring, vague atmosphere, or "the moment feels tense" unless the shot has a concrete visual turn.
6.3. Make silent beats transactional and consequential: someone wants something, hides something, blocks something, misunderstands something, or forces a decision through action."""
        if _no_dialogue_enabled() else
        """5. For each scene, write the FULL dialogue word-for-word in the character's voice.
5.5. Do NOT use ultra-short placeholder dialogue for scenes that visibly last several seconds. If a spoken scene runs longer than a quick reaction beat, write enough full sentences to naturally fill the scene once without the video model needing to repeat itself.
6. Give each dialogue line a purpose: reaction, request, refusal, deflection, correction, confession, joke-under-pressure, or decision.
6.1. For every spoken scene, fill dialogue_intent, dialogue_obstacle, dialogue_subtext, dialogue_turn, and forbidden_smalltalk BEFORE writing the final dialogue.
6.2. Ban soft filler loops. Do not repeat "findest du?", "spürst du das?", "was denkst du?", "dieser Moment", "diese Spannung", "wir zwei hier", or generic feelings unless the scene turns that phrase into a concrete conflict or joke.
6.3. Make dialogue transactional and consequential: someone wants something, hides something, blocks something, misunderstands something, or forces a decision."""
    )
    duration_check_step = (
        f"16. Double-check: add up all estimated durations after clamping each scene to {SCENE_MAX_SEC}s max. You must output at least {min_scene_count or 1} scenes, preferably around {ideal_scene_count or min_scene_count or 1}. If under the planning target, ADD MORE SILENT SCENES; do not assume one scene can exceed {SCENE_MAX_SEC}s."
        if _no_dialogue_enabled() else
        f"16. Double-check: add up all estimated durations after clamping each scene to {SCENE_MAX_SEC}s max. You must output at least {min_scene_count or 1} scenes, preferably around {ideal_scene_count or min_scene_count or 1}. If under the planning target, ADD MORE SCENES and fuller dialogue/action; do not assume one scene can exceed {SCENE_MAX_SEC}s."
    )
    comic_planning_step = (
        "11. If the brief is funny, sarcastic, absurd, or mockumentary-like, build a real comedy machine instead of generic awkwardness: every scene must contain a serious documentary claim, a visible absurd contradiction, one overconfident mistake or misunderstanding that makes the problem worse, and one carried clue/object/consequence that can return later. Use wrong-object focus, bureaucratic overconfidence, physical cause-and-effect, escalating mishaps, deadpan narration against ridiculous images, and callbacks. Do not invent recurring tiny insects, flower petals, moths, bees, ladybugs, or random landing bugs unless the user explicitly asked for them."
        if _no_dialogue_enabled() else
        "11. If the brief is funny, sarcastic, absurd, or mockumentary-like, build a real comedy machine instead of generic awkwardness: every scene must contain a serious documentary claim, a visible absurd contradiction, one overconfident mistake or misunderstanding that makes the problem worse, and one carried clue/object/consequence that can return later. Use wrong-object focus, bureaucratic overconfidence, physical cause-and-effect, escalating mishaps, deadpan narration against ridiculous images, and callbacks."
    )
    comedy_engine_steps = (
        """11.1. For comedy/mockumentary/satire, fill these fields in EVERY scene:
  - comedy_claim: the serious documentary statement or expectation the scene pretends is true.
  - comedy_contradiction: the visible absurd fact that disproves the claim.
  - escalation_mistake: the exact action, bad interpretation, or false solution that makes the situation worse.
  - visible_evidence_object: for mystery/investigation plots, one concrete visible clue or physical proof that pushes the next scene (hose, pipe, gauge, water trail, cable, stain, tool, receipt, lever, pressure meter, etc.).
  - payoff_object_or_callback: a prop, stain, phrase, sound, damage, or tiny consequence that either came from an earlier scene or will return later.
11.2. Avoid flat scene functions like "reveal the problem" unless the reveal also creates a joke and a new consequence. Each scene should end with either a sharper suspicion, a worse physical mess, a false clue, a reversed accusation, or a callback becoming more absurd.
11.3. Dialogue in comedy should be a joke under pressure, denial, accusation, bad excuse, official-sounding nonsense, or a decision that causes the next visual disaster. Avoid merely explaining what the image already shows."""
    )
    planning_prompt = f"""USER BRIEF: {brief}

{composition_lock_line}
{"TARGET DURATION: " + str(target_dur) + " seconds (" + f"{target_dur/60:.1f}" + " minutes)." if target_dur else ""}
{"SCENE COUNT REQUIREMENT: Output at least " + str(min_scene_count) + " scenes, and preferably around " + str(ideal_scene_count) + " scenes, because every generated clip is clamped to " + str(SCENE_MAX_SEC) + " seconds maximum." if min_scene_count and ideal_scene_count else ""}
{first_pass_duration_line}
{no_dialogue_planning_line}

Think step by step:
1. What is the overall narrative arc? Beginning, middle, end.
2. Write the story_world first: the premise, central conflict, character wants, escalation rule, recurring motif/running joke, cause-and-effect chain, and payoff direction. For comedy, define the repeatable joke engine, not just the topic.
{duration_planning_step}
4. Build a cause-and-effect chain. For every scene after scene 1, state what it inherits from the previous scene, what concrete mistake/choice changes by the end, and which prop/line/consequence carries forward.
{dialogue_planning_steps}
{camera_planning_step}
8. For each scene, decide what the visible subject is physically doing, what the camera is doing, and what one frozen hero moment best captures the scene.
9. Specify concrete pose details: hand placement, arm angle, shoulder tension, torso turn, leg stance, gaze direction, expression, posture.
10. Specify object interaction details: what is being held, touched, leaned on, pointed at, worn, or manipulated, and how the body connects to it.
10.5. Copy forward any explicit physical traits from the user's brief as non-negotiable anchors: exact body build, asymmetry, scars, facial features, handedness, props in a specific hand, or other body-part-specific details.
{comic_planning_step}
{comedy_engine_steps}
12. For each scene, include one comic_hook: a single bizarre prop detail, stain, snack, tool choice, background mishap, smug gesture, or ironic contradiction that makes the image unforgettable.
{"12.1. In no-dialogue mode, comic_hook should be a meaningful prop/action/gesture beat, not a tiny decorative insect, petal, moth, ladybug, bee, or random nature speck." if _no_dialogue_enabled() else ""}
13. Prefer surprising, memorable visual business over vague confusion. Do not default to blank staring unless it directly sets up a stronger payoff.
14. Write continuity anchors (wardrobe, set, lighting, props) so every scene is visually consistent.
15. Write story continuity anchors (unresolved question, consequence, emotional state, object/line callback) so every scene feels caused by the previous one. In comedy, at least one callback or physical consequence should visibly escalate across multiple scenes. In investigation/mystery plots, every scene after the opening should follow or reveal a visible evidence object rather than jumping by narrator logic.
{duration_check_step}
{"17. On the first pass, err slightly long. A plan that lands around " + str(planning_target) + " seconds is better than one that comes in short and has to be rebuilt." if planning_target else ""}

Shot and framing bias controls:
{get_prompt_bias_section()}

After your thinking, output ONLY the final JSON object with characters, voices, style, story_world, locations, and scenes."""
    if _natural_dialogue_enabled():
        planning_prompt += (
            "\n\nNatural dialogue mode:\n"
            "- Keep spoken lines grounded, conversational, and believable.\n"
            "- Prefer natural phrasing over catchphrases, speeches, or showy monologues unless the brief explicitly wants that.\n"
            "- Let comedy come from behavior and situation as much as from wording.\n"
            "- Natural does not mean idle smalltalk: every spoken line still needs pressure, subtext, or a concrete turn.\n"
        )

    _last_planning_debug = {
        "mode": "brief_breakdown",
        "system_prompt": _breakdown_system_prompt(),
        "planning_prompt": planning_prompt,
        "target_duration_seconds": target_dur,
        "estimated_wpm": wpm,
        "initial_raw_response": "",
        "rewrite_attempts": [],
    }

    raw = _chat_with_auto_tokens(
        model=OLLAMA_MODEL_CREATIVE,
        messages=[
            {"role": "system", "content": _breakdown_system_prompt()},
            {"role": "user", "content": planning_prompt},
        ],
        base_options=_breakdown_llm_options({"temperature": 1.0}),
        start_tokens=16384,
        max_tokens=65536,
    )
    _last_planning_debug["initial_raw_response"] = raw
    scenes = _apply_composition_lock_to_scenes(
        _strip_dialogue_from_scenes(_ensure_story_continuity_fields(_parse_json(raw, retries=2, brief=brief))),
        brief,
    )

    # Phase 2: Calculate duration from dialogue + action
    for s in scenes:
        s["duration_seconds"] = calc_scene_duration(s, wpm)

        dialogue = s.get("dialogue", "")
        word_count = len(dialogue.replace('"', '').split()) if dialogue else 0
        action_sec = s.get("action_seconds", 0)
        log.info("  Scene %d: %d words dialogue (%.1fs @ %dwpm) + %ds action = %ds",
                 s["scene_number"], word_count,
                 (word_count / wpm * 60) if word_count else 0,
                 wpm, action_sec, s["duration_seconds"])
        s["status"] = "pending"

    total_dur_planned = sum(s["duration_seconds"] for s in scenes)
    log.info("Planned %d scenes, total ~%ds (%.1f min)", len(scenes), total_dur_planned, total_dur_planned / 60)

    # Phase 3: If undershooting target, have the model REWRITE the entire plan (not append)
    max_rewrites = 2
    for rewrite_attempt in range(max_rewrites):
        if not target_dur or total_dur_planned >= target_dur * 0.9:
            break

        shortfall = target_dur - total_dur_planned
        log.warning("Undershoot! Planned %ds but need %ds (short by %ds). Rewrite %d/%d...",
                     total_dur_planned, target_dur, shortfall,
                     rewrite_attempt + 1, max_rewrites)

        # Build a per-scene breakdown so the model sees exactly what it wrote and what each timed to
        scene_lines = []
        for s in scenes:
            dlg = s.get("dialogue", "")
            wc = len(dlg.replace('"', '').split()) if dlg else 0
            scene_lines.append(f"  Scene {s['scene_number']}: {s['duration_seconds']}s ({wc} words dialogue)")
        scene_breakdown = "\n".join(scene_lines)
        current_count = len(scenes)
        missing_count = max(0, (ideal_scene_count or min_scene_count or current_count) - current_count)

        if _no_dialogue_enabled():
            rewrite_prompt = f"""Your silent scene plan is TOO SHORT. Here's what happened:

{scene_breakdown}
TOTAL: {total_dur_planned} seconds — but the target is {target_dur} seconds ({target_dur/60:.1f} minutes).
You are SHORT by {shortfall} seconds.
You output {current_count} scenes, but this duration model needs at least {min_scene_count or '?'} scenes and should use about {ideal_scene_count or '?'} scenes because every clip is capped at {SCENE_MAX_SEC}s. You need roughly {missing_count} additional scene(s), placed throughout the middle and ending, not simply appended.

REWRITE THE ENTIRE PLAN from scratch as a silent film plan. Do NOT just append scenes at the end — that creates bad pacing.
Instead:
- Keep every dialogue field exactly "".
- Add new silent scenes in the MIDDLE of the narrative, not just at the end.
- Output at least {ideal_scene_count or min_scene_count or current_count} scenes total.
- Set most action_seconds to {max(1, SCENE_MAX_SEC - 2)} so they calculate close to {SCENE_MAX_SEC}s, but vary a few shorter beats for pacing.
- Expand the story arc through visible cause-and-effect: decisions, mistakes, reveals, reactions, object handling, physical comedy, blocking, chase/arrival/setup/payoff beats.
- Increase action_seconds for scenes that can genuinely sustain longer visual business.
- Add substance, not filler: every added beat must create pressure, reveal information, force a decision, dodge a truth visually, or pay off a concrete object/action.
- Use ambient sound and action-tied sound effects only; no speech, narration, whispers, murmurs, voice-over, or talking.
- Aim slightly long this time. Landing around {planning_target or target_dur} seconds is preferable to another undershoot.
- Remember: duration must come from action_seconds and visual scene count because no dialogue is allowed.

	You need at least {min_scene_count or '?'} scenes and preferably around {ideal_scene_count or '?'} scenes to hit {target_dur}s after clamping.
	
	Output the COMPLETE rewritten JSON object with characters, voices, style, story_world, locations, and scenes. No preamble."""
        else:
            rewrite_prompt = f"""Your scene plan is TOO SHORT. Here's what happened:

{scene_breakdown}
TOTAL: {total_dur_planned} seconds — but the target is {target_dur} seconds ({target_dur/60:.1f} minutes).
You are SHORT by {shortfall} seconds.
You output {current_count} scenes, but this duration model needs at least {min_scene_count or '?'} scenes and should use about {ideal_scene_count or '?'} scenes because every clip is capped at {SCENE_MAX_SEC}s. You need roughly {missing_count} additional scene(s), placed throughout the middle and ending, not simply appended.

REWRITE THE ENTIRE PLAN from scratch. Do NOT just append scenes at the end — that creates bad pacing.
Instead:
- Add more dialogue to existing scenes (longer monologues, more back-and-forth)
- Add new scenes in the MIDDLE of the narrative, not just at the end
- Output at least {ideal_scene_count or min_scene_count or current_count} scenes total.
- Expand the story arc — add more topics, more tangents, more reactions
- Every scene with dialogue: write MORE words so each scene runs longer
- Replace tiny one-line placeholder dialogue with fuller, in-character spoken lines that can naturally fill the shot once without repetition
- Add substance, not filler: each added sentence must create pressure, reveal information, force a decision, dodge a truth, or pay off a concrete object/action in the scene
- Do not pad with repeated "findest du?", "spürst du das?", "wie fühlst du dich?", "dieser Moment", or generic romantic atmosphere
- Aim slightly long this time. Landing around {planning_target or target_dur} seconds is preferable to another undershoot.
- Remember: dialogue duration = word_count / {wpm} WPM × 60 seconds

	You need at least {min_scene_count or '?'} scenes and preferably around {ideal_scene_count or '?'} scenes to hit {target_dur}s after clamping.
	
	Output the COMPLETE rewritten JSON object with characters, voices, style, story_world, locations, and scenes. No preamble."""
        if _natural_dialogue_enabled():
            rewrite_prompt += (
                "\n\nNatural dialogue mode:\n"
                "- Expand spoken lines with believable conversational detail, not theatrical speeches.\n"
                "- Avoid forced catchphrases or overly polished punchlines unless the brief explicitly wants them.\n"
                "- Do not fill duration with soft agreement, repeated questions, or generic feelings.\n"
            )

        raw = _chat_with_auto_tokens(
            model=OLLAMA_MODEL_CREATIVE,
            messages=[
                {"role": "system", "content": _breakdown_system_prompt()},
                {"role": "user", "content": planning_prompt},
                {"role": "assistant", "content": raw},
                {"role": "user", "content": rewrite_prompt},
            ],
            base_options=_breakdown_llm_options({"temperature": 1.0}),
            start_tokens=16384,
            max_tokens=65536,
        )
        _last_planning_debug["rewrite_attempts"].append({
            "attempt": rewrite_attempt + 1,
            "rewrite_prompt": rewrite_prompt,
            "raw_response": raw,
        })
        try:
            scenes = _apply_composition_lock_to_scenes(
                _strip_dialogue_from_scenes(_ensure_story_continuity_fields(_parse_json(raw, retries=1, brief=brief))),
                brief,
            )
            for s in scenes:
                s["duration_seconds"] = calc_scene_duration(s, wpm)
                s["status"] = "pending"
                dialogue = s.get("dialogue", "")
                word_count = len(dialogue.replace('"', '').split()) if dialogue else 0
                action_sec = s.get("action_seconds", 0)
                log.info("  Scene %d (rewrite): %d words dialogue, %ds",
                         s["scene_number"], word_count, s["duration_seconds"])

            total_dur_planned = sum(s["duration_seconds"] for s in scenes)
            log.info("After rewrite: %d scenes, total ~%ds (%.1f min)",
                     len(scenes), total_dur_planned, total_dur_planned / 60)
        except Exception as e:
            log.warning("Rewrite parse failed, keeping previous plan: %s", e)
            break

    scenes = _renumber_scenes(scenes, wpm)
    scenes = _revise_duplicate_dialogue_scenes(scenes, brief, wpm)
    scenes = _expand_short_dialogue_scenes(scenes, brief, wpm)
    scenes = _revise_generic_dialogue_scenes(scenes, brief, wpm)
    scenes = _revise_duplicate_dialogue_scenes(scenes, brief, wpm)
    scenes = _strip_dialogue_from_scenes(scenes, wpm)
    scenes = _extend_scene_plan_to_target(scenes, target_dur, wpm)
    scenes = _renumber_scenes(_apply_composition_lock_to_scenes(scenes, brief), wpm)

    return scenes


# Module-level storage for character descriptions from the current breakdown
_current_characters = {}
_current_voices = {}
_current_style = ""
_current_story_world = ""
_current_locations = {}
_last_planning_debug = {}
CHARACTER_FOCUS = 68
ACTION_FOCUS = 62
ENVIRONMENT_WEIGHT = 58
ESTABLISHING_SHOT_BIAS = 32
NATURAL_DIALOGUE = bool(getattr(config, "NATURAL_DIALOGUE", False))
NO_DIALOGUE = bool(getattr(config, "NO_DIALOGUE", False))


def get_character_descriptions() -> dict:
    """Get the character descriptions from the most recent breakdown."""
    return _current_characters


def get_voice_descriptions() -> dict:
    """Get the voice descriptions from the most recent breakdown."""
    return _current_voices


def get_style_anchor() -> str:
    """Get the style anchor from the most recent breakdown."""
    return _current_style


def get_story_world() -> str:
    """Get the story/world continuity anchor from the most recent breakdown."""
    return _current_story_world


def get_location_anchors() -> dict:
    """Get canonical location/environment descriptions from the most recent breakdown."""
    return _current_locations


def get_last_planning_debug() -> dict:
    """Get debug information from the most recent planning pass."""
    return dict(_last_planning_debug)


def get_prompt_bias_controls() -> dict:
    return {
        "character_focus": int(CHARACTER_FOCUS),
        "action_focus": int(ACTION_FOCUS),
        "environment_weight": int(ENVIRONMENT_WEIGHT),
        "establishing_shot_bias": int(ESTABLISHING_SHOT_BIAS),
    }


def _natural_dialogue_enabled() -> bool:
    return bool(NATURAL_DIALOGUE)


def _no_dialogue_enabled() -> bool:
    return bool(NO_DIALOGUE)


def _breakdown_system_prompt() -> str:
    prompt = BREAKDOWN_SYSTEM
    if _natural_dialogue_enabled():
        prompt += NATURAL_DIALOGUE_BREAKDOWN_APPEND
    if _no_dialogue_enabled():
        prompt += NO_DIALOGUE_BREAKDOWN_APPEND
    return prompt


def _script_parse_system_prompt() -> str:
    prompt = SCRIPT_PARSE_SYSTEM
    if _natural_dialogue_enabled():
        prompt += NATURAL_DIALOGUE_BREAKDOWN_APPEND
    if _no_dialogue_enabled():
        prompt += NO_DIALOGUE_BREAKDOWN_APPEND
    return prompt


def _prompt_writer_system_prompt() -> str:
    prompt = PROMPT_WRITER_SYSTEM_SAFE if SUBTITLE_SAFE_MODE else PROMPT_WRITER_SYSTEM
    if _natural_dialogue_enabled():
        prompt += NATURAL_DIALOGUE_PROMPT_APPEND
    if _no_dialogue_enabled():
        prompt += NO_DIALOGUE_PROMPT_APPEND
    return prompt


def _bias_level(value: int) -> str:
    if value >= 80:
        return "very high"
    if value >= 60:
        return "high"
    if value >= 40:
        return "medium"
    if value >= 20:
        return "low"
    return "very low"


def get_prompt_bias_section() -> str:
    controls = get_prompt_bias_controls()
    lines = [
        "PROMPT PRIORITIES:",
        f"- Character focus: {controls['character_focus']}/100 ({_bias_level(controls['character_focus'])})",
        f"- Action focus: {controls['action_focus']}/100 ({_bias_level(controls['action_focus'])})",
        f"- Environment weight: {controls['environment_weight']}/100 ({_bias_level(controls['environment_weight'])})",
        f"- Establishing shot bias: {controls['establishing_shot_bias']}/100 ({_bias_level(controls['establishing_shot_bias'])})",
    ]

    if controls["character_focus"] >= 60:
        lines.append("- Keep visible people as the dominant visual subject whenever a character is present.")
    elif controls["character_focus"] <= 35:
        lines.append("- Characters do not need to dominate the frame if the setting or composition is more important.")

    if controls["action_focus"] >= 60:
        lines.append("- Prioritize the key physical action or dramatic beat over atmosphere or empty setup.")
    elif controls["action_focus"] <= 35:
        lines.append("- Atmosphere and composition may take priority over showing the peak action moment.")

    if controls["environment_weight"] >= 65:
        lines.append("- Preserve architecture, set dressing, terrain, and spatial layout very faithfully from the environment anchors.")
    elif controls["environment_weight"] <= 35:
        lines.append("- Keep environment continuity, but do not let location detail dominate the shot.")

    if controls["establishing_shot_bias"] >= 60:
        lines.append("- Wide establishing compositions are welcome when they suit the scene.")
    elif controls["establishing_shot_bias"] <= 35:
        lines.append("- Avoid defaulting to room-first or landscape-first establishing shots unless explicitly required by the scene.")

    return "\n".join(lines)


def _dialogue_intent(dialogue: str, max_words: int = 28) -> str:
    """Compress literal dialogue into a short spoken-content intent summary."""
    if not dialogue:
        return ""
    clean = re.sub(r'\s+', ' ', dialogue).strip()
    clean = clean.replace('"', '').replace("'", "")
    words = clean.split()
    if len(words) <= max_words:
        return clean
    return " ".join(words[:max_words]) + " ..."


def _compact_anchor(text: str, max_words: int) -> str:
    """Keep canonical anchors concise so they support the scene instead of overwhelming it."""
    clean = re.sub(r"\s+", " ", (text or "")).strip()
    if not clean:
        return ""
    words = clean.split()
    if len(words) <= max_words:
        return clean
    return " ".join(words[:max_words]) + " ..."


def _scene_story_bits(scene: dict) -> str:
    bits = []
    labels = (
        ("purpose", "story_purpose"),
        ("from previous", "transition_from_previous"),
        ("turn", "new_information_or_turn"),
        ("state", "character_state"),
        ("callback/setup", "callback_or_setup"),
        ("comedy claim", "comedy_claim"),
        ("comic contradiction", "comedy_contradiction"),
        ("escalation mistake", "escalation_mistake"),
        ("visible evidence", "visible_evidence_object"),
        ("payoff/callback object", "payoff_object_or_callback"),
        ("dialogue intent", "dialogue_intent"),
        ("dialogue obstacle", "dialogue_obstacle"),
        ("dialogue turn", "dialogue_turn"),
    )
    for label, key in labels:
        value = re.sub(r"\s+", " ", str(scene.get(key, "") or "")).strip()
        if value and value.lower() != "none":
            bits.append(f"{label}: {value}")
    return "; ".join(bits)


def _previous_scene_handoff(prev_scene: dict | None) -> str:
    if not prev_scene:
        return ""
    parts = []
    for key in ("description", "new_information_or_turn", "character_state", "callback_or_setup",
                "comedy_contradiction", "escalation_mistake", "payoff_object_or_callback",
                "visible_evidence_object",
                "continuity_notes"):
        value = re.sub(r"\s+", " ", str(prev_scene.get(key, "") or "")).strip()
        if value and value.lower() != "none":
            parts.append(value)
    dialogue = "" if _no_dialogue_enabled() else _dialogue_anchor_excerpt(str(prev_scene.get("dialogue", "") or ""), max_words=16)
    if dialogue:
        parts.append(f'previous spoken beat: "{dialogue}"')
    return _compact_anchor(" ".join(parts), 70)


def _ensure_story_continuity_fields(scenes: list[dict]) -> list[dict]:
    scenes = _renumber_scenes(scenes)
    prev = None
    for scene in scenes:
        description = _compact_anchor(scene.get("description", ""), 28)
        action = _compact_anchor(scene.get("action_description", ""), 22)
        mood = str(scene.get("mood", "") or "").strip()
        chars = ", ".join(str(item) for item in scene.get("characters_in_scene", []) or [])

        scene.setdefault("story_purpose", "")
        scene.setdefault("transition_from_previous", "")
        scene.setdefault("new_information_or_turn", "")
        scene.setdefault("character_state", "")
        scene.setdefault("callback_or_setup", "")
        scene.setdefault("comedy_claim", "")
        scene.setdefault("comedy_contradiction", "")
        scene.setdefault("escalation_mistake", "")
        scene.setdefault("visible_evidence_object", "")
        scene.setdefault("payoff_object_or_callback", "")
        scene.setdefault("dialogue_intent", "")
        scene.setdefault("dialogue_obstacle", "")
        scene.setdefault("dialogue_subtext", "")
        scene.setdefault("dialogue_turn", "")
        scene.setdefault("forbidden_smalltalk", "")

        if not str(scene.get("story_purpose") or "").strip():
            scene["story_purpose"] = f"Advance the film through this beat: {description or action}"
        if not str(scene.get("transition_from_previous") or "").strip():
            if prev:
                handoff = _previous_scene_handoff(prev)
                scene["transition_from_previous"] = f"Responds to the previous beat: {handoff}"
            else:
                scene["transition_from_previous"] = "Opening beat that establishes the central situation and first visible tension."
        if not str(scene.get("new_information_or_turn") or "").strip():
            scene["new_information_or_turn"] = action or description
        if not str(scene.get("character_state") or "").strip():
            if chars and mood:
                scene["character_state"] = f"{chars} carry a {mood} emotional tone into this beat."
            elif mood:
                scene["character_state"] = f"The scene carries a {mood} emotional tone."
        if not str(scene.get("callback_or_setup") or "").strip():
            callback = scene.get("comic_hook") or scene.get("continuity_notes") or ""
            if callback and str(callback).strip().lower() != "none":
                scene["callback_or_setup"] = _compact_anchor(str(callback), 24)
        if not str(scene.get("payoff_object_or_callback") or "").strip():
            callback = scene.get("callback_or_setup") or scene.get("comic_hook") or ""
            if callback and str(callback).strip().lower() != "none":
                scene["payoff_object_or_callback"] = _compact_anchor(str(callback), 24)
        if scene.get("dialogue") and not str(scene.get("dialogue_intent") or "").strip():
            scene["dialogue_intent"] = f"Make this spoken beat change the scene: {scene.get('new_information_or_turn') or action or description}"
        if scene.get("dialogue") and not str(scene.get("dialogue_obstacle") or "").strip():
            scene["dialogue_obstacle"] = scene.get("transition_from_previous") or "The speaker needs a concrete response, decision, or change."
        if scene.get("dialogue") and not str(scene.get("dialogue_turn") or "").strip():
            scene["dialogue_turn"] = scene.get("new_information_or_turn") or "The relationship or situation must be different after this line."
        if scene.get("dialogue") and not str(scene.get("forbidden_smalltalk") or "").strip():
            scene["forbidden_smalltalk"] = "Do not fill this beat with generic feelings, repeated soft questions, or neutral smalltalk."
        prev = scene
    return _renumber_scenes(scenes)


_POSE_HINTS = (
    "hand", "hands", "finger", "fingers", "arm", "arms", "elbow", "elbows",
    "shoulder", "shoulders", "torso", "waist", "hip", "hips", "leg", "legs",
    "knee", "knees", "foot", "feet", "stance", "posture", "pose", "gaze",
    "looking", "looks", "stares", "expression", "jaw", "mouth", "smile",
    "grimace", "lean", "leans", "leaning", "crouch", "crouches", "kneel",
    "kneels", "twist", "twists", "turn", "turns", "raised", "lowered",
    "clenched", "hunched", "upright", "bent"
)

_OBJECT_HINTS = (
    "hold", "holds", "holding", "grip", "grips", "gripping", "grab", "grabs",
    "touch", "touches", "touching", "lean on", "leans on", "point", "points",
    "pointing", "wear", "wears", "wearing", "carry", "carries", "carrying",
    "lift", "lifts", "lifting", "push", "pushes", "pull", "pulls", "brace",
    "braces", "press", "presses", "open", "opens", "close", "closes",
    "manipulate", "manipulates", "use", "uses", "rests on", "rests against"
)


def _extract_detail_clauses(*texts: str, hints: tuple[str, ...]) -> str:
    """Pull short clauses that contain body/prop detail cues from nearby scene text."""
    matches: list[str] = []
    seen: set[str] = set()
    for text in texts:
        clean = re.sub(r"\s+", " ", (text or "")).strip()
        if not clean:
            continue
        chunks = re.split(r"(?<=[.!?])\s+|;\s+|,\s+(?=(?:with|while|as|his|her|their|the|a|an|left|right)\b)", clean)
        for chunk in chunks:
            snippet = chunk.strip(" .")
            lowered = f" {snippet.lower()} "
            if not snippet:
                continue
            if not any(hint in lowered for hint in hints):
                continue
            key = lowered.strip()
            if key in seen:
                continue
            seen.add(key)
            matches.append(snippet)
    return "; ".join(matches[:4])


def _scene_subject_action(scene: dict) -> str:
    text = (scene.get("subject_action") or "").strip()
    if text:
        return text
    pose = (scene.get("pose_details") or "").strip()
    obj = (scene.get("object_interaction") or "").strip()
    if pose and obj:
        return f"{pose}; {obj}"
    if pose:
        return pose
    if obj:
        return obj
    return (scene.get("action_description") or scene.get("description") or "").strip()


def _scene_camera_action(scene: dict) -> str:
    text = (scene.get("camera_action") or "").strip()
    if text:
        return text
    action = (scene.get("action_description") or "").strip()
    lowered = action.lower()
    camera_markers = ("camera ", "zoom", "pan", "dolly", "tracking", "handheld", "push in", "pull back", "tilt")
    if any(marker in lowered for marker in camera_markers):
        return action
    return ""


def _scene_hero_moment(scene: dict) -> str:
    text = (scene.get("hero_moment") or "").strip()
    if text:
        return text
    subject = _scene_subject_action(scene)
    if subject:
        return subject
    return (scene.get("description") or "").strip()


def _scene_pose_details(scene: dict) -> str:
    text = (scene.get("pose_details") or "").strip()
    if text:
        return text
    return _extract_detail_clauses(
        scene.get("hero_moment", ""),
        scene.get("subject_action", ""),
        scene.get("action_description", ""),
        scene.get("description", ""),
        hints=_POSE_HINTS,
    )


def _scene_object_interaction(scene: dict) -> str:
    text = (scene.get("object_interaction") or "").strip()
    if text:
        return text
    return _extract_detail_clauses(
        scene.get("hero_moment", ""),
        scene.get("subject_action", ""),
        scene.get("action_description", ""),
        scene.get("description", ""),
        hints=_OBJECT_HINTS + ("left hand", "right hand"),
    )


def _scene_comic_hook(scene: dict) -> str:
    text = (scene.get("comic_hook") or "").strip()
    if text:
        if _no_dialogue_enabled() and _is_tiny_nature_hook(text):
            return ""
        return text

    fallback_sources = (
        (scene.get("object_interaction") or "").strip(),
        (scene.get("hero_moment") or "").strip(),
        (scene.get("subject_action") or "").strip(),
        (scene.get("action_description") or "").strip(),
    )
    memorable_keywords = (
        "wrench", "tool", "snack", "mustard", "leberwurst", "stain", "grease",
        "mud", "soggy", "crooked", "bent", "wrong", "broken", "mismatched",
        "bucket", "hose", "oar", "life jacket", "lifejacket", "thermos",
        "megaphone", "duct tape", "helmet", "clipboard", "sweater", "smirk",
    )
    for source in fallback_sources:
        lowered = source.lower()
        if source and any(keyword in lowered for keyword in memorable_keywords):
            return source
    return ""


def _dialogue_anchor_excerpt(dialogue: str, max_words: int = 12) -> str:
    """Keep a short verbatim excerpt as a language anchor without pasting full dialogue."""
    if not dialogue:
        return ""
    clean = re.sub(r"\s+", " ", dialogue).strip()
    words = clean.split()
    excerpt = " ".join(words[:max_words])
    if len(words) > max_words:
        excerpt += " ..."
    return excerpt


_VIDEO_FINAL_MARKERS = (
    "drafting the final text",
    "drafting the final prompt",
    "drafting the final scene text",
    "final prompt",
    "final text",
    "final polish",
)

_VIDEO_META_MARKERS = (
    "check word count",
    "final word count check",
    "check ",
    "self-correction",
    "drafting opening",
    "expanding environment",
    "adding character",
    "dialogue placement",
    "style integration",
    "environment construction",
    "visual style check",
    "is it 300-500 words",
    "let's expand",
    "let's tighten",
    "let's place",
    "wait,",
    "i need to",
    "i will",
    "let's go",
    "opening (the",
    "my opening",
    "this works",
    "proceeding to generate",
    "prompt structure check",
    "ready.",
    "ready:",
    "refining:",
    "constraint:",
    "character check",
    "setting check",
    "lighting check",
    "style check",
    "action check",
    "current draft construction",
    "verbatim check",
    "double checking",
    "the prompt is ready",
    "final scan for",
    "narrator dialogue",
    "narrator voice",
    "text construction",
    "expanded draft",
    "step 1:",
    "step 2:",
    "step 3:",
    "step 4:",
    "step 5:",
    "this looks solid",
)

_LTX_SECTION_HEADINGS = (
    "scene",
    "camera",
    "action",
    "performance",
    "environment",
    "motion",
    "audio",
    "lighting",
    "style",
    "constraints",
    "character",
    "dialogue",
    "continuity",
)


def _clean_video_prompt_paragraph(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^\s*```(?:text)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```\s*$", "", cleaned)
    cleaned = cleaned.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"'", '"'}:
        cleaned = cleaned[1:-1].strip()
    cleaned = cleaned.replace("**", "").replace("*", "").replace("`", "")
    cleaned = re.sub(r"^\s*[-•]\s*", "", cleaned)
    return cleaned.strip()


def _looks_like_video_meta(paragraph: str) -> bool:
    lowered = paragraph.lower()
    if not lowered:
        return True
    if lowered.startswith("ltx video 2.3"):
        return True
    if lowered.startswith("a single text prompt"):
        return True
    if lowered.startswith("the prompt must"):
        return True
    if lowered.startswith("scene ") and len(lowered) < 60:
        return True
    if any(marker in lowered for marker in _VIDEO_META_MARKERS):
        return True
    if lowered.startswith("character (") or lowered.startswith("visual style:") or lowered.startswith("primary moment"):
        return True
    return False


def _extract_structured_video_prompt(text: str) -> str:
    """Preserve LTX-friendly sectioned prompts instead of collapsing them into prose only."""
    cleaned_lines = []
    heading_hits = 0
    for raw_line in (text or "").replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = _clean_video_prompt_paragraph(raw_line)
        if not line:
            cleaned_lines.append("")
            continue
        lowered = line.lower().strip()
        if lowered.startswith((
            "check:",
            "word count check",
            "final word count check",
            "step 1:",
            "step 2:",
            "step 3:",
            "step 4:",
            "step 5:",
            "expanded draft:",
            "text construction:",
        )):
            continue
        if re.match(r"^(?:" + "|".join(re.escape(h) for h in _LTX_SECTION_HEADINGS) + r")\s*:", lowered):
            heading_hits += 1
        cleaned_lines.append(line)

    if heading_hits < 4:
        return ""
    structured = "\n".join(cleaned_lines).strip()
    structured = re.sub(r"\n{3,}", "\n\n", structured)
    if _video_prompt_word_count(structured) < 120:
        return ""
    return structured.strip()


def _extract_final_video_prompt(raw: str) -> str:
    """Recover the final prose prompt when the model leaks draft notes or self-corrections."""
    text = (raw or "").strip()
    if not text:
        return ""

    # Best case: the model wrapped the actual final prose in a long quoted block.
    quoted_candidates = []
    for match in re.finditer(r'"([^"\n]{220,})"', text, flags=re.DOTALL):
        candidate = _clean_video_prompt_paragraph(match.group(1))
        if candidate and not _looks_like_video_meta(candidate):
            quoted_candidates.append(candidate)
    if quoted_candidates:
        quoted_candidates.sort(key=len, reverse=True)
        return quoted_candidates[0].strip()

    lowered = text.lower()
    marker_pos = -1
    for marker in _VIDEO_FINAL_MARKERS:
        pos = lowered.rfind(marker)
        if pos > marker_pos:
            marker_pos = pos
    if marker_pos >= 0:
        text = text[marker_pos:]
        text = re.sub(r"^[^:\n]*:\s*", "", text, count=1)

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r'^\s*final prompt must[^"\n]*"\s*->\s*', "", text, flags=re.IGNORECASE)
    text = re.sub(r'^\s*ready\.\s*', "", text, flags=re.IGNORECASE)
    text = re.sub(r'^\s*let\'?s write it out\.?\s*', "", text, flags=re.IGNORECASE)
    for marker in (
        "current draft construction:",
        "the prompt is ready.",
        "opening:",
    ):
        pos = text.lower().rfind(marker)
        if pos >= 0:
            text = text[pos + len(marker):].lstrip()

    structured = _extract_structured_video_prompt(text)
    if structured:
        return structured

    paragraphs = [_clean_video_prompt_paragraph(p) for p in re.split(r"\n\s*\n", text) if p.strip()]

    good: list[str] = []
    for para in paragraphs:
        if _looks_like_video_meta(para):
            continue
        if para.count(".") + para.count("!") + para.count("?") < 2:
            continue
        if len(para) < 160:
            continue
        good.append(para)

    if good:
        joined = "\n\n".join(good).strip()
        joined = re.sub(r"^(?:let'?s go\.?\s*)+", "", joined, flags=re.IGNORECASE)
        joined = re.sub(r"^(?:final word count check:?\s*)+", "", joined, flags=re.IGNORECASE)
        return joined.strip()

    # Fallback: keep only non-bullet prose-like lines from the tail of the response.
    lines = []
    for line in text.splitlines():
        cleaned = _clean_video_prompt_paragraph(line)
        if not cleaned or _looks_like_video_meta(cleaned):
            continue
        if len(cleaned) < 60:
            continue
        lines.append(cleaned)
    if lines:
        joined = "\n".join(lines[-8:]).strip()
        joined = re.sub(r"^(?:let'?s go\.?\s*)+", "", joined, flags=re.IGNORECASE)
        joined = re.sub(r"^(?:final word count check:?\s*)+", "", joined, flags=re.IGNORECASE)
        return joined.strip()

    cleaned = _clean_video_prompt_paragraph(text)
    cleaned = re.sub(r"^(?:let'?s go\.?\s*)+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^(?:final word count check:?\s*)+", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _video_prompt_word_count(text: str) -> int:
    return len([w for w in re.findall(r"\b\w+\b", text or "") if any(ch.isalpha() for ch in w)])


def _video_prompt_needs_repair(prompt: str, raw: str, scene: dict) -> bool:
    words = _video_prompt_word_count(prompt)
    lowered = (prompt or "").lower()
    raw_lower = (raw or "").lower()
    dialogue = (scene.get("dialogue") or "").strip()
    normalized_prompt = re.sub(r"\s+", " ", (prompt or "")).strip()
    normalized_dialogue = re.sub(r"\s+", " ", dialogue).strip()

    if words < 120:
        return True
    if normalized_dialogue and normalized_prompt == normalized_dialogue:
        return True
    if any(marker in lowered for marker in _VIDEO_META_MARKERS):
        return True
    if any(marker in raw_lower for marker in ("word count check", "double check", "character check", "constraint check", "current draft construction")) and words < 260:
        return True
    if dialogue and '"' not in prompt and "'" not in prompt and not SUBTITLE_SAFE_MODE:
        return True
    return False


def _repair_video_prompt_from_draft(raw_prompt: str, scene: dict, context: str) -> str:
    repair_request = (
        "Turn the following draft, outline, or partial answer into ONE finished cinematic LTX scene prompt.\n\n"
        "Requirements:\n"
        "- Keep all concrete scene information, dialogue, voice cues, sound cues, props, action, and bodily details.\n"
        "- Remove checklist labels, notes to self, word-count comments, 'Included.', 'Ready.', 'Double check', and similar meta text.\n"
        "- Do NOT shorten aggressively. Rich scene prose is desirable if it remains visually and aurally useful.\n"
        "- Clear section headings such as Scene, Camera, Action, Performance, Environment, Audio, Lighting, Style, and Constraints are allowed.\n"
        "- The result must feel like a finished scene prompt, not a planning outline.\n"
        "- Respond with ONLY the finished scene prompt text.\n\n"
        f"SCENE CONTEXT:\n{context}\n\n"
        f"DRAFT TO REWRITE:\n{raw_prompt}"
    )
    if _no_dialogue_enabled():
        repair_request += (
            "\n\nSILENT MODE OVERRIDE: write a nonverbal visual-performance prompt. "
            "Keep facial expression and body language visually closed-mouth. "
            "Use a clear instrumental ambient music bed plus environmental foley."
        )
    response = llm_chat(
        model=OLLAMA_MODEL_CREATIVE,
        messages=[
            {"role": "system", "content": _prompt_writer_system_prompt()},
            {"role": "user", "content": repair_request},
        ],
        options=_prompt_llm_options({
            "temperature": 0.8,
            "num_predict": 6144 if config.llm_creative_drafting_enabled() else 4096,
            "num_ctx": 16384,
        }),
    )
    return _extract_final_video_prompt(response["message"]["content"].strip())


def _build_deterministic_video_prompt(scene: dict, prev_scene: dict = None, brief: str = "") -> str:
    """Last-resort finished LTX prompt when the LLM returns only dialogue or draft notes."""
    characters = get_character_descriptions()
    voices = get_voice_descriptions()
    chars_in_scene = scene.get("characters_in_scene", []) or list(characters.keys())
    style = get_style_anchor()
    story_world = get_story_world()
    location_id, location_anchor = _location_anchor_for_scene(scene)
    hero_moment = _scene_hero_moment(scene)
    subject_action = _scene_subject_action(scene)
    camera_action = _scene_camera_action(scene)
    pose_details = _scene_pose_details(scene)
    object_interaction = _scene_object_interaction(scene)
    comic_hook = _scene_comic_hook(scene)
    previous_handoff = _previous_scene_handoff(prev_scene)
    dialogue = "" if _no_dialogue_enabled() else str(scene.get("dialogue", "") or "").strip()
    hero_text = str(hero_moment or scene.get("description", "the main scene beat")).strip().rstrip(".")
    action_text = str(subject_action or scene.get("action_description", "the character reacting in the moment")).strip().rstrip(".")

    sections: list[str] = []
    opening = (
        f"{scene.get('shot_type', 'Cinematic shot')} opens on {hero_text}. "
        f"The main visible action is {action_text}."
    )
    sections.append(f"Scene:\n{opening}")

    character_text = ""
    if chars_in_scene and characters:
        char_lines = []
        for char_id in chars_in_scene:
            desc = characters.get(char_id, "")
            if desc:
                char_lines.append(f"{char_id}: {desc}")
        if char_lines:
            character_text = " ".join(char_lines)
    if character_text:
        sections.append(f"Character:\n{character_text}")

    camera_bits = [str(scene.get("shot_type", "cinematic shot"))]
    if camera_action:
        camera_bits.append(camera_action)
    sections.append("Camera:\n" + ". ".join(bit.strip().rstrip(".") for bit in camera_bits if bit).strip() + ".")

    action_bits = [
        f"0:00-0:03 {hero_text}",
        f"0:03-0:06 {action_text}",
    ]
    if pose_details:
        action_bits.append(f"Body pose remains specific: {pose_details}")
    if object_interaction:
        action_bits.append(f"Object contact remains specific: {object_interaction}")
    if comic_hook:
        action_bits.append(f"Distinctive visual hook: {comic_hook}")
    sections.append("Action:\n" + "\n".join(f"- {bit.strip().rstrip('.')}." for bit in action_bits if bit))

    performance_bits = []
    if dialogue:
        voice_bits = []
        for char_id in chars_in_scene:
            voice = voices.get(char_id, "")
            if voice:
                voice_bits.append(f"{char_id}: {voice}")
        if voice_bits:
            performance_bits.append("Voice direction: " + " ".join(voice_bits))
        if SUBTITLE_SAFE_MODE:
            intent = _dialogue_intent(dialogue)
            language = _infer_spoken_language(dialogue, brief)
            performance_bits.append(
                f"The character speaks naturally in {language}, preserving the original language, about this content: {intent}. "
                "The words are heard as speech, never rendered as subtitles or readable text."
            )
        else:
            performance_bits.append(f'The spoken dialogue is delivered once, naturally and clearly: "{dialogue}"')
    else:
        if _no_dialogue_enabled():
            performance_bits.append("Silent nonverbal performance plays through visible action, ambient sound, and character reaction.")
        else:
            performance_bits.append("There is no spoken dialogue; the scene plays through visible action, ambient sound, and character reaction.")
    if previous_handoff:
        performance_bits.append(f"Carryover from previous scene: {previous_handoff}")
    if story_world:
        performance_bits.append(f"Story subtext: {_compact_anchor(story_world, 45)}")
    sections.append("Performance:\n" + " ".join(performance_bits))

    environment_bits = []
    if location_anchor:
        environment_bits.append(_compact_anchor(location_anchor, 70))
    elif scene.get("setting_description"):
        environment_bits.append(str(scene.get("setting_description")))
    if scene.get("continuity_notes"):
        environment_bits.append(f"Continuity: {scene.get('continuity_notes')}")
    if environment_bits:
        sections.append("Environment:\n" + " ".join(environment_bits))

    audio_description = scene.get("audio_description")
    if _no_dialogue_enabled():
        audio_description = _silent_audio_description(audio_description, scene)
    if audio_description:
        sections.append(f"Audio:\n{audio_description}")
    if scene.get("lighting_description"):
        sections.append(f"Lighting:\n{scene.get('lighting_description')}")
    if style:
        sections.append(f"Style:\n{_compact_anchor(style, 36)}")

    constraints = [
        f"The scene lasts about {scene.get('duration_seconds', SCENE_SWEET_SPOT_SEC)} seconds.",
        "Keep concrete action, full character reconstruction, setting, lighting, camera, sound, and continuity.",
    ]
    if _no_dialogue_enabled():
        constraints.append("Closed-mouth nonverbal performance with a gentle instrumental ambient music bed and environmental foley.")
    elif not SUBTITLE_SAFE_MODE:
        constraints.append("Do not repeat the spoken line to fill time; let body language, pauses, sound, and camera movement carry the remaining duration.")
    else:
        constraints.append("Do not render readable text, captions, subtitles, lower thirds, or typography overlays anywhere in the frame.")
    sections.append("Constraints:\n" + " ".join(constraints))

    prompt = "\n\n".join(section.strip() for section in sections if section and section.strip())
    return _scrub_no_dialogue_prompt(prompt) if _no_dialogue_enabled() else prompt


def _breakdown_llm_options(base_options: dict | None = None) -> dict:
    opts = dict(base_options or {})
    opts.update(config.llm_reasoning_options(for_breakdown=True))
    return opts


def _prompt_llm_options(base_options: dict | None = None) -> dict:
    opts = dict(base_options or {})
    opts.update(config.llm_reasoning_options(for_breakdown=False))
    return opts


def _creative_drafting_guidance(scene: dict) -> str:
    if not config.llm_creative_drafting_enabled():
        return ""
    mood = (scene.get("mood") or "").strip()
    comic_hook = _scene_comic_hook(scene)
    hook_line = ""
    if comic_hook:
        hook_line = f"- Keep this memorable scene hook alive and visible if possible: {comic_hook}\n"
    return (
        "CREATIVE DRAFTING MODE:\n"
        "- You may think like an inventive director on the page before landing the final shot description.\n"
        "- Favor playful, surprising, film-literate detail, comic contrast, awkward escalation, overconfident failure, and strong performative beats.\n"
        "- Prefer scenes that contain one or two unforgettable odd details rather than generic atmosphere.\n"
        "- Treat props, stains, snacks, wrong tools, background mishaps, and badly timed reactions as opportunities for comedy if they fit the scene.\n"
        "- Longer rich prose is welcome if it stays visual, audible, and scene-specific.\n"
        "- Do not compress the prompt too early; LTX benefits from dense scenic, behavioral, tonal, and sensory information.\n"
        "- Associative or abstract supporting phrases are acceptable if they intensify the same scene rather than drifting into a new one.\n"
        "- The final answer should resolve into one strong cinematic scene prompt, not a sterile minimal prompt.\n"
        "- Do NOT leave behind checklist headers such as Character Check, Setting Check, Constraint Check, Opening, Ready, or Final Scan.\n"
        f"{hook_line}"
        f"- Current mood emphasis: {mood or 'not specified'}.\n"
    )


def _infer_spoken_language(dialogue: str, brief: str = "") -> str:
    """Best-effort spoken language hint for subtitle-safe mode."""
    text = f"{dialogue} {brief}".lower()
    if not text.strip():
        return "the original language requested by the user"

    language_markers = [
        ("German", [" der ", " die ", " das ", " und ", " nicht ", " ist ", " auf deutsch", " deutsch", "guten", "danke"]),
        ("English", [" the ", " and ", " is ", " are ", " with ", " in english", " english "]),
        ("French", [" le ", " la ", " les ", " est ", " avec ", " bonjour", " merci", " en francais", " en français", " français"]),
        ("Spanish", [" el ", " la ", " los ", " las ", " que ", " gracias", " hola", " en espanol", " en español", " español"]),
        ("Italian", [" il ", " lo ", " gli ", " che ", " grazie", " ciao", " in italiano", " italiano "]),
        ("Portuguese", [" nao ", " não ", " obrigado", " olá", " voce ", " você ", " em portugues", " em português", " português"]),
    ]

    padded = f" {text} "
    for language, markers in language_markers:
        if any(marker in padded for marker in markers):
            return language
    return "the original language used in the source dialogue"


def _tone_guidance(brief: str, scene: dict) -> str:
    """Return tonal instructions so playful briefs do not collapse into flat documentary prose."""
    mood = (scene.get("mood") or "").lower()
    text = f"{brief} {scene.get('description', '')} {scene.get('dialogue', '')} {mood}".lower()
    directives: list[str] = []
    comic_mode = False

    if any(word in text for word in ("funny", "witzig", "komisch", "humor", "humour", "lustig")):
        directives.append("- Play the moment for visual comedy, not neutral coverage.")
        comic_mode = True
    if any(word in text for word in ("sarcastic", "sarkast", "deadpan", "mockumentary", "mockumentary", "satire", "satir")):
        directives.append("- Keep an ironic, deadpan mockumentary tone; the framing should feel observant but amused.")
        comic_mode = True
    if any(word in text for word in ("absurd", "chaotic", "chaos", "awkward", "trottelig", "clumsy", "unfall", "crash", "fails", "peinlich", "embarrass")):
        directives.append("- Emphasize awkward escalation, comic timing, and the split-second before or after the mishap.")
        comic_mode = True
    if any(word in mood for word in ("funny", "sarcastic", "absurd", "chaotic")):
        directives.append("- Avoid solemn prestige-film language; let the action feel specific, playful, and slightly exaggerated while staying visually plausible.")
        comic_mode = True

    if comic_mode:
        contrast_line = (
            "- Let the humor come from contrast: serious documentary framing around ridiculous silent behavior, overconfident body language during obvious failure, or elegant composition around petty chaos."
            if _no_dialogue_enabled() else
            "- Let the humor come from contrast: serious documentary framing around ridiculous behavior, overconfident delivery during obvious failure, or elegant narration over petty chaos."
        )
        directives.extend([
            "- Prefer active comic business over passive blankness: bungled gestures, misplaced confidence, tiny disasters, stubborn prop handling, or hilariously wrong focus.",
            "- Use memorable concrete details and props when they help the joke land: food, tools, stains, scraps, odd sounds, background mishaps, or badly timed reactions.",
            "- Do not reduce the performance to generic 'confused' or 'awkward' unless the confusion is visibly doing something funny right now.",
            contrast_line,
        ])

    if not directives:
        return ""

    return "TONAL ENERGY:\n" + "\n".join(dict.fromkeys(directives))


def _sanitize_scene(s: dict) -> dict:
    """Coerce scene fields to correct types. LLMs sometimes output numbers as strings."""
    # Integer fields
    for key in ("scene_number", "action_seconds", "duration_seconds"):
        if key in s:
            try:
                s[key] = int(float(str(s[key])))
            except (ValueError, TypeError):
                s[key] = 0

    # dialogue might be a list of dicts from some models — fix BEFORE string coercion
    if isinstance(s.get("dialogue"), list):
        parts = []
        for item in s["dialogue"]:
            if isinstance(item, dict):
                parts.append(item.get("text", item.get("line", str(item))))
            else:
                parts.append(str(item))
        s["dialogue"] = " ".join(parts)

    # String fields that might come as other types
    for key in ("description", "dialogue", "action_description", "subject_action",
                "camera_action", "hero_moment", "comic_hook", "pose_details", "object_interaction", "shot_type",
                "mood", "audio_description", "setting_description",
                "lighting_description", "continuity_notes", "location_id",
                "story_purpose", "transition_from_previous", "new_information_or_turn",
                "character_state", "callback_or_setup", "comedy_claim",
                "comedy_contradiction", "escalation_mistake", "visible_evidence_object",
                "payoff_object_or_callback",
                "dialogue_intent",
                "dialogue_obstacle", "dialogue_subtext", "dialogue_turn",
                "forbidden_smalltalk"):
        if key in s and not isinstance(s[key], str):
            s[key] = str(s[key]) if s[key] is not None else ""

    # characters_in_scene should be a list
    if "characters_in_scene" in s and not isinstance(s["characters_in_scene"], list):
        s["characters_in_scene"] = [str(s["characters_in_scene"])]

    # Ensure scene_number exists
    if "scene_number" not in s:
        s["scene_number"] = 0

    return s


def _fix_json(raw: str) -> str:
    """Fix common LLM JSON issues. Handles all known failure patterns from Gemma."""
    import re

    # 1. Flatten all whitespace (newlines inside keys/values break JSON)
    raw = raw.replace('\r\n', ' ').replace('\r', ' ').replace('\n', ' ').replace('\t', ' ')

    # 2. Collapse multiple spaces
    raw = re.sub(r'  +', ' ', raw)

    # 3. Remove single-line comments (// ...)
    raw = re.sub(r'//.*?(?=[,}\]\n])', '', raw)

    # 4. Remove trailing commas before } or ]
    raw = re.sub(r',\s*([}\]])', r'\1', raw)

    # 5. Fix unquoted string values after colon
    #    Catches: "key":_wide  "key":value  "key": value_here
    #    But NOT: "key": 123  "key": true  "key": false  "key": null  "key": [  "key": {  "key": "
    raw = re.sub(
        r':\s*(?!")(?![\d\[\{tfn-])([a-zA-Z_][a-zA-Z0-9_ ]*?)(?=\s*[,}\]])',
        r': "\1"',
        raw
    )

    # 6. Fix unquoted keys before colon
    #    Catches: {key: "value"}  , key: "value"
    raw = re.sub(
        r'([{\[,])\s*(?!")([a-zA-Z_][a-zA-Z0-9_]*?)\s*:',
        r'\1 "\2":',
        raw
    )

    # 7. Fix single-quoted strings -> double quotes
    #    Catches: 'value' -> "value" (but careful not to break apostrophes in text)
    #    Only do this for key positions (before colons)
    raw = re.sub(r"'([^']{1,50}?)'\s*:", r'"\1":', raw)

    # 8. Fix missing comma between objects inside arrays: } {  or } "
    #    Only inside arrays (after a ]), not at top level
    raw = re.sub(r'}\s*\{', '}, {', raw)
    # Don't fix } " at top level as it breaks {characters}{scenes} -> wrap detection

    # 9. Remove any BOM or zero-width characters
    raw = raw.replace('\ufeff', '').replace('\u200b', '')

    # 10. Fix truncated JSON — balance unclosed brackets/braces
    open_braces = raw.count('{') - raw.count('}')
    open_brackets = raw.count('[') - raw.count(']')
    if open_brackets > 0 or open_braces > 0:
        # Truncated output — close everything
        raw = raw.rstrip().rstrip(',')  # Remove trailing comma if any
        raw += ']' * open_brackets + '}' * open_braces

    return raw


def _try_parse_json(raw: str):
    """Try to parse JSON with progressively more aggressive fixes."""
    # Attempt 1: raw parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Attempt 2: fix trailing commas and comments
    fixed = _fix_json(raw)
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass

    # Attempt 3: replace single quotes with double quotes
    fixed2 = fixed.replace("'", '"')
    try:
        return json.loads(fixed2)
    except json.JSONDecodeError:
        pass

    # Give up — raise the original error
    return json.loads(raw)


def _parse_json(raw: str, retries: int = 2, brief: str = "") -> list[dict]:
    """Parse JSON from Gemma output, retrying on failure.

    Handles both formats:
    - Plain array: [scene1, scene2, ...]
    - Dict with characters: {"characters": {...}, "scenes": [...]}
    """
    global _current_characters, _current_voices, _current_style, _current_story_world, _current_locations

    # Strip markdown fences if present
    if "```" in raw:
        lines = raw.split("\n")
        inside = False
        cleaned = []
        for line in lines:
            if line.strip().startswith("```"):
                inside = not inside
                continue
            if inside:
                cleaned.append(line)
        raw = "\n".join(cleaned)

    # Find JSON start
    first_brace = raw.find("{")
    first_bracket = raw.find("[")
    if first_brace >= 0 and (first_bracket < 0 or first_brace < first_bracket):
        raw = raw[first_brace:]
        # Find matching close
        end = raw.rfind("}") + 1
        if end > 0:
            raw = raw[:end]
    elif first_bracket >= 0:
        raw = raw[first_bracket:]
        end = raw.rfind("]") + 1
        if end > 0:
            raw = raw[:end]

    try:
        parsed = _try_parse_json(raw)

        # Handle {characters, scenes} dict format
        scenes = None

        if isinstance(parsed, dict):
            if "characters" in parsed:
                _current_characters = parsed["characters"]
                log.info("Loaded %d character descriptions", len(_current_characters))
            if "voices" in parsed:
                _current_voices = parsed["voices"]
                log.info("Loaded %d voice descriptions", len(_current_voices))
            else:
                # Fallback: use known voice anchors for recognized characters
                for char_id in _current_characters:
                    char_lower = char_id.lower()
                    for name, anchor in VOICE_ANCHORS.items():
                        if name in char_lower:
                            _current_voices[char_id] = anchor
                            break
                if _current_voices:
                    log.info("Applied %d fallback voice anchors", len(_current_voices))
            if "style" in parsed and isinstance(parsed["style"], str):
                _current_style = parsed["style"]
                log.info("Loaded style anchor: %s", _current_style[:80])
            elif not _current_style:
                _current_style = "Photorealistic cinematic style, natural lighting, shallow depth of field, film-quality color grading."
                log.info("Using default style anchor")
            story_value = parsed.get("story_world", parsed.get("story_bible", parsed.get("story_lock", "")))
            if isinstance(story_value, dict):
                story_value = "; ".join(f"{key}: {value}" for key, value in story_value.items())
            if isinstance(story_value, str) and story_value.strip():
                _current_story_world = story_value.strip()
                log.info("Loaded story world anchor: %s", _current_story_world[:100])
            if "locations" in parsed and isinstance(parsed["locations"], dict):
                _current_locations = {
                    str(key): str(value).strip()
                    for key, value in parsed["locations"].items()
                    if str(key).strip() and str(value).strip()
                }
                if _current_locations:
                    log.info("Loaded %d location anchors", len(_current_locations))
            if "scenes" in parsed:
                scenes = parsed["scenes"]
            else:
                # Fallback: maybe it's a weird format, look for any list value
                for v in parsed.values():
                    if isinstance(v, list):
                        scenes = v
                        break
                if scenes is None:
                    raise ValueError("Dict has no 'scenes' key")
        elif isinstance(parsed, list):
            scenes = parsed
        else:
            raise ValueError(f"Unexpected JSON type: {type(parsed)}")

        # Sanitize all scenes — coerce types, fix dialogue format, etc.
        return [_sanitize_scene(s) for s in scenes]

    except (json.JSONDecodeError, ValueError) as e:
        if retries <= 0:
            raise ValueError(f"Gemma returned invalid JSON after retries: {e}\nRaw: {raw[:500]}")
        # Log the area around the error for debugging
        if isinstance(e, json.JSONDecodeError):
            pos = e.pos if hasattr(e, 'pos') else 0
            start = max(0, pos - 100)
            end = min(len(raw), pos + 100)
            log.warning("JSON parse failed at position %d: %s", pos, e.msg)
            log.warning("Context around error: ...%s<<<ERROR HERE>>>%s...",
                        raw[start:pos], raw[pos:end])
        else:
            log.warning("JSON parse failed: %s", e)
        log.info("Retrying Gemma call...")
        response = llm_chat(
            model=OLLAMA_MODEL_CREATIVE,
            messages=[
                {"role": "system", "content": _breakdown_system_prompt()},
                {"role": "user", "content": brief},
                {"role": "assistant", "content": raw},
                {"role": "user", "content": 'That was not valid JSON. Respond with ONLY the JSON object: {"characters": {...}, "voices": {...}, "style": "...", "story_world": "...", "locations": {...}, "scenes": [...]}. No markdown fences.'},
            ],
            options=_breakdown_llm_options({"temperature": 0.3}),
        )
        return _parse_json(response["message"]["content"].strip(), retries - 1, brief)


def _location_anchor_for_scene(scene: dict) -> tuple[str, str]:
    """Return (location_id, canonical_description) for a scene when available."""
    location_id = (scene.get("location_id") or "").strip()
    if location_id:
        anchor = (_current_locations or {}).get(location_id, "").strip()
        if anchor:
            return location_id, anchor
    return location_id, ""


# ── Prompt Writing ─────────────────────────────────────────────────────────

def write_prompt(scene: dict, prev_scene: dict = None, brief: str = "") -> str:
    """Write an LTX 2.3 prompt for a single scene."""

    # Get character descriptions from the breakdown
    characters = get_character_descriptions()
    voices = get_voice_descriptions()
    chars_in_scene = scene.get("characters_in_scene", [])

    context = f"=== SCENE {scene['scene_number']} ===\n\n"

    # STYLE ANCHOR — visual style lock for consistency across all scenes
    style = get_style_anchor()
    if style:
        context += f"VISUAL STYLE LOCK (compressed):\n  {_compact_anchor(style, 20)}\n\n"

    story_world = get_story_world()
    if story_world:
        context += f"STORY WORLD LOCK (compressed):\n  {_compact_anchor(story_world, 90)}\n\n"

    composition_lock = get_composition_lock(brief)
    if composition_lock:
        context += (
            "COMPOSITION / POSE LOCK — non-negotiable user framing constraint:\n"
            f"  {composition_lock}\n"
            "  Preserve this before style, action, or cinematic variety. Any motion must remain inside this locked frame.\n\n"
        )

    previous_handoff = _previous_scene_handoff(prev_scene)
    current_story_bits = _scene_story_bits(scene)
    if previous_handoff or current_story_bits:
        context += "STORY CONTINUITY — make this scene feel caused by the film around it:\n"
        if previous_handoff:
            context += f"  Previous scene handoff: {previous_handoff}\n"
        if current_story_bits:
            context += f"  Current scene story function: {current_story_bits}\n"
        context += (
            "  Use this as subtext, behavior, prop state, emotional carryover, or spoken reaction. "
            "Do not turn it into a visible title card or exposition dump.\n\n"
        )

    comedy_bits = []
    for label, key in (
        ("serious claim to undercut", "comedy_claim"),
        ("visible contradiction", "comedy_contradiction"),
        ("mistake that worsens the beat", "escalation_mistake"),
        ("visible evidence object", "visible_evidence_object"),
        ("payoff/callback object", "payoff_object_or_callback"),
    ):
        value = re.sub(r"\s+", " ", str(scene.get(key, "") or "")).strip()
        if value and value.lower() != "none":
            comedy_bits.append(f"  {label}: {value}")
    if comedy_bits:
        context += (
            "COMEDY ENGINE — preserve the sketch mechanics, not just the plot:\n"
            + "\n".join(comedy_bits)
            + "\n  Stage the serious claim and absurd contradiction in the same shot whenever possible; let the mistake create the next consequence.\n\n"
        )

    context += get_prompt_bias_section() + "\n\n"
    tone_guidance = _tone_guidance(brief, scene)
    if tone_guidance:
        context += tone_guidance + "\n\n"
    creative_guidance = _creative_drafting_guidance(scene)
    if creative_guidance:
        context += creative_guidance + "\n\n"

    # Lead with the dynamic core of the scene before worldbuilding details.
    hero_moment = _scene_hero_moment(scene)
    subject_action = _scene_subject_action(scene)
    camera_action = _scene_camera_action(scene)
    comic_hook = _scene_comic_hook(scene)
    pose_details = _scene_pose_details(scene)
    object_interaction = _scene_object_interaction(scene)
    context += "PRIMARY MOMENT — open the prompt with this action-first cinematic beat:\n"
    context += f"  Hero moment: {hero_moment}\n"
    context += f"  Shot type: {scene['shot_type']}\n"
    context += f"  Mood: {scene['mood']}\n"

    if subject_action:
        context += f"  Subject action / pose to emphasize: {subject_action}\n"
    if pose_details:
        context += f"  Specific pose details to preserve: {pose_details}\n"
    if object_interaction:
        context += f"  Object interaction details to preserve: {object_interaction}\n"
    if comic_hook:
        context += f"  Memorable comic or ironic detail to preserve: {comic_hook}\n"
    if scene.get("comedy_contradiction"):
        context += f"  Comic contradiction to make visible: {scene.get('comedy_contradiction')}\n"
    if scene.get("escalation_mistake"):
        context += f"  Mistake/escalation to make playable: {scene.get('escalation_mistake')}\n"
    if scene.get("visible_evidence_object"):
        context += f"  Visible evidence object to include: {scene.get('visible_evidence_object')}\n"
    if scene.get("payoff_object_or_callback"):
        context += f"  Callback/payoff object to carry: {scene.get('payoff_object_or_callback')}\n"
    if camera_action:
        context += f"  Camera action to imply: {camera_action}\n"
    context += (
        "  The first sentences of the final prompt must establish the core visual event, "
        "the main subject, and the motion or dramatic change happening right now.\n"
        "  The opening should describe a visible pose, gesture, object interaction, or moment of impact, not just a place.\n"
        "  Do not begin with background geography or broad environment description unless the scene itself is explicitly an establishing shot.\n\n"
    )

    if pose_details or object_interaction:
        context += "NON-NEGOTIABLE BODY / PROP CONSTRAINTS:\n"
        if pose_details:
            context += f"  Preserve these exact body-part details in visible form: {pose_details}\n"
        if object_interaction:
            context += f"  Preserve this exact prop/body contact in visible form: {object_interaction}\n"
        context += (
            "  These are required visual facts of the shot. Do not generalize them into vague motion.\n\n"
        )

    if comic_hook:
        context += "MEMORABLE COMIC HOOK:\n"
        context += f"  Preserve this oddly specific detail, contradiction, prop choice, stain, snack, tool, or ironic flourish if it fits the shot: {comic_hook}\n"
        context += (
            "  This hook should make the image more distinctive and funny, not pull it into a different scene.\n\n"
        )

    # CHARACTER(S) — full descriptions every time
    if chars_in_scene and characters:
        context += "CHARACTERS IN THIS SCENE (include these FULL descriptions VERBATIM in your prompt):\n"
        appearance_lock_lines = []
        for char_id in chars_in_scene:
            desc = characters.get(char_id, "")
            if desc:
                context += f"  {char_id}: {desc}\n"
                appearance_lock_lines.append(f"{char_id}: {desc}")
            else:
                context += f"  {char_id}: (no description found — describe them fully yourself)\n"
        context += "\n"
        if appearance_lock_lines:
            context += "APPEARANCE CONTINUITY LOCK — non-negotiable clothing, hairstyle, and body attributes:\n"
            for line in appearance_lock_lines:
                context += f"  {line}\n"
            context += "Preserve these exact visual identity facts in this scene; do not simplify or swap outfit, hair, body proportions, face, or skin tone. Preserve footwear when visible, unless the priority note below says it is optional.\n\n"
            optional_footwear = get_optional_footwear_guidance(brief)
            if optional_footwear:
                context += f"FOOTWEAR PRIORITY NOTE:\n  {optional_footwear}\n\n"
    elif characters:
        # No explicit list but we have characters — include all of them
        context += "CHARACTERS (include FULL descriptions VERBATIM for any that appear):\n"
        for char_id, desc in characters.items():
            context += f"  {char_id}: {desc}\n"
        context += "\n"
        context += "APPEARANCE CONTINUITY LOCK — preserve each appearing character's exact clothing, hairstyle, body attributes, face, and skin tone from the descriptions above. Preserve footwear when visible unless a priority note says it is optional.\n\n"
        optional_footwear = get_optional_footwear_guidance(brief)
        if optional_footwear:
            context += f"FOOTWEAR PRIORITY NOTE:\n  {optional_footwear}\n\n"
    else:
        context += "CHARACTERS: No pre-written descriptions available. You MUST write a detailed, specific physical description of every person in the scene (face, body, clothing, age, skin, hair, build) and include it in full.\n\n"

    # VOICE ANCHORS — only useful when speech is intentionally allowed.
    if not _no_dialogue_enabled():
        scene_voices = {}
        for char_id in (chars_in_scene or characters.keys()):
            v = voices.get(char_id, "")
            if v:
                scene_voices[char_id] = v
            else:
                # Try fallback voice anchors for known figures
                char_lower = char_id.lower()
                for name, anchor in VOICE_ANCHORS.items():
                    if name in char_lower:
                        scene_voices[char_id] = anchor
                        break
        if scene_voices:
            context += "VOICE ANCHORS (describe HOW each character speaks — the video model generates speech audio):\n"
            for char_id, voice_desc in scene_voices.items():
                context += f"  {char_id}: {voice_desc}\n"
            if _natural_dialogue_enabled():
                context += (
                    "Include voice characteristics in a restrained, human way when describing dialogue. "
                    "Favor subtle cadence and natural tone over actorly flourish.\n\n"
                )
            else:
                context += "Include voice characteristics in your prompt when describing dialogue (e.g. 'says in a deep, gravelly New York accent')\n\n"

    # SETTING — full world description every time
    location_id, location_anchor = _location_anchor_for_scene(scene)
    setting = scene.get('setting_description', '')
    continuity = scene.get('continuity_notes', '')
    lighting = scene.get('lighting_description', '')
    context += "WORLD SUPPORTING DETAILS (use after the opening action beat):\n"
    if location_anchor:
        context += f"  Canonical location anchor ({location_id}, compressed): {_compact_anchor(location_anchor, 34)}\n"
    if setting:
        context += f"  Scene-specific environment details: {setting}\n"
    if lighting:
        context += f"  Lighting: {lighting}\n"
    if continuity:
        context += f"  Continuity anchors: {continuity}\n"
    if current_story_bits:
        context += f"  Story continuity anchors: {current_story_bits}\n"
    context += "\n"

    # CAMERA
    context += f"CAMERA: {scene['shot_type']}\n"
    if camera_action:
        context += f"CAMERA MOVEMENT / ENERGY: {camera_action}\n"
    context += f"MOOD/ATMOSPHERE: {scene['mood']}\n\n"

    # ACTION
    if subject_action:
        context += f"SUBJECT ACTION / BODY LANGUAGE: {subject_action}\n\n"
    elif scene.get('action_description'):
        context += f"PHYSICAL ACTION/BODY LANGUAGE: {scene.get('action_description')}\n\n"
    if pose_details:
        context += f"POSE DETAILS TO EXPLICITLY INCLUDE: {pose_details}\n\n"
    if object_interaction:
        context += f"OBJECT INTERACTION TO EXPLICITLY INCLUDE: {object_interaction}\n\n"

    # DIALOGUE — exact words
    dialogue = scene.get("dialogue", "")
    if _no_dialogue_enabled():
        dialogue = ""
        context += (
            "SILENT MODE: this is a closed-mouth nonverbal visual scene with an instrumental ambient music bed.\n"
            "Stage the beat through visible action, face, posture, timing, object handling, environmental foley, and music texture.\n\n"
        )
    elif dialogue and SUBTITLE_SAFE_MODE:
        intent = _dialogue_intent(dialogue)
        language = _infer_spoken_language(dialogue, brief)
        anchor_excerpt = _dialogue_anchor_excerpt(dialogue)
        context += "SPOKEN CONTENT (subtitle-safe mode):\n"
        context += (
            f"  The character speaks naturally in {language}, in their own voice, about this content: {intent}\n"
            "  Keep the speech audible and emotionally appropriate.\n"
            "  Preserve the original spoken language from the source dialogue exactly. Do NOT translate, anglicize, or switch languages.\n"
        )
        if anchor_excerpt:
            context += (
                f"  Language anchor excerpt from the source dialogue: {anchor_excerpt}\n"
                "  Use this only to preserve spoken language and phrasing flavor. The words must be heard as speech, not shown as readable text.\n"
            )
        context += (
            "  DO NOT show any readable words, captions, subtitles, or on-screen text.\n\n"
        )
    elif dialogue:
        context += f"DIALOGUE (include these EXACT words in quotes — do NOT summarize or shorten):\n{dialogue}\n\n"
    else:
        context += "DIALOGUE: None — this is a visual/ambient scene.\n\n"

    dialogue_engine = []
    for label, key in (
        ("Intent", "dialogue_intent"),
        ("Obstacle", "dialogue_obstacle"),
        ("Subtext", "dialogue_subtext"),
        ("Turn after the line", "dialogue_turn"),
        ("Forbidden smalltalk", "forbidden_smalltalk"),
    ):
        value = str(scene.get(key, "") or "").strip()
        if value:
            dialogue_engine.append(f"  {label}: {value}")
    if dialogue_engine and not _no_dialogue_enabled():
        context += "DIALOGUE ENGINE — use this to stage speech as action, not filler:\n"
        context += "\n".join(dialogue_engine) + "\n"
        context += (
            "  The spoken line should change the scene or reveal pressure. "
            "Do not make the scene feel like idle conversation.\n\n"
        )

    if _natural_dialogue_enabled() and not _no_dialogue_enabled():
        context += (
            "NATURAL DIALOGUE PRIORITY:\n"
            "- Spoken lines should feel like believable speech captured in the moment.\n"
            "- Avoid turning every line into a polished monologue, slogan, or wink at the audience.\n"
            "- Keep delivery grounded and conversational unless the scene explicitly demands something bigger.\n"
            "- Make the line react to the previous beat or reveal a concrete want, worry, dodge, or decision.\n"
            "- Prefer specific, slightly imperfect phrasing over generic statements that could fit any scene.\n\n"
        )

    # AUDIO
    audio = scene.get('audio_description', scene.get('audio_notes', ''))
    if _no_dialogue_enabled():
        audio = _silent_audio_description(audio, scene)
    if audio:
        context += f"SOUND/AUDIO: {audio}\n\n"

    context += f"DURATION: {scene['duration_seconds']} seconds\n\n"

    context += (
        "LTX OUTPUT STRUCTURE — write the final prompt with concise headings:\n"
        "Scene: one compact paragraph naming the first-frame anchor, subject, location, and emotional state.\n"
        "Camera: lens/framing/movement, with smooth motion language.\n"
        "Action: 2-5 time-coded beats covering the duration, using visible body motion and object contact.\n"
        "Performance: expression, micro-gestures, emotional pressure, and silent nonverbal delivery when no dialogue is present.\n"
        "Environment: location continuity, props, background, weather or room behavior.\n"
        "Audio: ambient sound and action-tied sound effects; dialogue/voice only if explicitly present and no-dialogue mode is off.\n"
        "Lighting: source, direction, contrast, color temperature, and shadow behavior.\n"
        "Style: compact visual style, texture, color, depth of field, and film/camera feel.\n"
        "Constraints: continuity anchors and any negative visual constraints.\n\n"
    )

    if _no_dialogue_enabled():
        speech_reminder = (
            "- Keep the character's lips relaxed or closed, with expression carried by eyes, posture, gesture, and timing\n"
            "- If a line would normally be present, replace it with facial expression, gesture, posture, prop handling, and camera timing\n"
            "- Prefer concrete funny behavior over vague incompetence: specific mishaps, wrong priorities, absurd prop choices, smug silent recovery, or deadpan framing over visible failure"
        )
    else:
        speech_reminder = (
            "- Include voice characteristics when characters speak\n"
            "- If dialogue is provided, the spoken line should be delivered once naturally within the scene; do not repeat, restate, or echo the same sentence to fill time\n"
            "- Prefer concrete funny behavior over vague incompetence: specific mishaps, wrong priorities, absurd prop choices, smug recovery, deadpan narration over visible failure"
        )

    context += f"""CRITICAL REMINDERS:
- Rebuild the ENTIRE world from scratch — character, setting, lighting, props, camera
- The video model has NEVER seen any other scene. Describe EVERYTHING.
- Include the FULL character description — never use "he", "she", or "the same person"
- Include the FULL setting — never write "same room" or "same stage"
- Include the visual style anchor — weave the style description into the scene naturally
- Carry forward the previous scene's consequence, emotional residue, prop state, or setup when story continuity is provided
{speech_reminder}
- Make the subject's visible action, pose, gesture, and object interaction unmistakable
- Preserve exact hand placement, limb position, gaze direction, and prop contact when provided
- Preserve explicit bodily traits from the source brief and character descriptions exactly; do not genericize anatomy, physique, facial structure, or handedness
- Keep the prose tight, playable, and imageable; avoid flat repetition and generic filler
- If the scene is comic, awkward, absurd, or sarcastic, the humor must be visible in behavior, timing, framing, and contrast
- Avoid defaulting to passive blank staring unless it clearly sets up a stronger visual joke in the same moment
- Preserve any provided comic_hook or absurdly specific memorable detail; let it sharpen the scene's identity rather than sanding it off
- Longer prompts are allowed and often desirable when they add usable scene substance: behavior, props, sound, contrast, timing, and visual texture
- Favor dense, scene-relevant detail over minimalism; do not trim away interesting information just to be concise
- 420-900 words is acceptable when the material stays vivid, specific, and playable rather than checklist-like."""

    if _no_dialogue_enabled():
        context += "\n- Keep the final scene prompt framed as closed-mouth nonverbal performance with instrumental ambient music and environmental foley."
        context += "\n- The audio bed should be clearly music/ambience/foley rather than human vocal texture."
    elif SUBTITLE_SAFE_MODE:
        context += "\n- Do NOT render readable text, captions, subtitles, lower thirds, or typography overlays anywhere in the frame."
    else:
        context += "\n- Embed all dialogue word-for-word in quotes within the action"
    if _natural_dialogue_enabled():
        context += "\n- Keep dialogue sounding natural and lived-in; underplay performance unless the brief explicitly asks for broad delivery."
        context += "\n- Avoid stumpf, generic dialogue: every line should have pressure, subtext, or a reason to be spoken now."

    response = llm_chat(
        model=OLLAMA_MODEL_CREATIVE,
        messages=[
            {"role": "system", "content": _prompt_writer_system_prompt()},
            {"role": "user", "content": context},
        ],
        options=_prompt_llm_options({
            "temperature": 1.0,
            "num_predict": 6144 if config.llm_creative_drafting_enabled() else 4096,
            "num_ctx": 16384 if config.llm_creative_drafting_enabled() else 12288,
        }),
    )
    raw_prompt = response["message"]["content"].strip()
    prompt = _extract_final_video_prompt(raw_prompt)
    if prompt and prompt != raw_prompt:
        log.warning("Scene %d prompt writer returned draft/meta text; extracted final prose prompt (%d -> %d chars).",
                    scene["scene_number"], len(raw_prompt), len(prompt))
    if _video_prompt_needs_repair(prompt, raw_prompt, scene):
        repaired = _repair_video_prompt_from_draft(raw_prompt, scene, context)
        if repaired and not _video_prompt_needs_repair(repaired, repaired, scene):
            log.warning("Scene %d prompt required repair pass (%d -> %d chars).",
                        scene["scene_number"], len(prompt), len(repaired))
            prompt = repaired
        else:
            fallback = _build_deterministic_video_prompt(scene, prev_scene=prev_scene, brief=brief)
            log.warning("Scene %d prompt writer stayed unusable after repair; using deterministic LTX fallback (%d words).",
                        scene["scene_number"], len(fallback.split()))
            prompt = fallback
    if not prompt:
        prompt = _build_deterministic_video_prompt(scene, prev_scene=prev_scene, brief=brief)
    if _no_dialogue_enabled():
        prompt = _scrub_no_dialogue_prompt(prompt)
    log.info("Wrote prompt for scene %d (%d words): %s...",
             scene["scene_number"], len(prompt.split()), prompt[:100])
    return prompt


# ── Retry Prompt ───────────────────────────────────────────────────────────

def write_retry_prompt(scene: dict, eval_result: dict, attempt: int) -> str:
    """Rewrite a prompt based on evaluation failure."""
    fail_reason = eval_result.get("fail_reason", "unknown")
    suggestion = eval_result.get("retry_suggestion", "try a different approach")

    context = f"""A video generation attempt failed evaluation.

Original scene: {scene['description']}
Original prompt: {scene.get('ltx_prompt', 'none')}
Failure reason: {fail_reason}
Retry suggestion: {suggestion}
Attempt number: {attempt} of 3

Write a NEW LTX 2.3 prompt for this scene that addresses the failure.
Respond with ONLY the new prompt text."""

    if SUBTITLE_SAFE_MODE and scene.get("dialogue"):
        language = _infer_spoken_language(scene.get("dialogue", ""))
        anchor_excerpt = _dialogue_anchor_excerpt(scene.get("dialogue", ""))
        context += f"\n\nKeep any spoken dialogue in {language}. Do NOT translate or switch languages."
        if anchor_excerpt:
            context += f"\nLanguage anchor excerpt: {anchor_excerpt}"
        context += "\nDo not render captions, subtitles, or readable on-screen text."
    if _natural_dialogue_enabled():
        context += (
            "\nKeep any rewritten spoken delivery grounded and conversational. "
            "Avoid turning the line into a theatrical monologue unless the brief explicitly calls for that."
        )

    response = llm_chat(
        model=OLLAMA_MODEL_CREATIVE,
        messages=[
            {"role": "system", "content": _prompt_writer_system_prompt()},
            {"role": "user", "content": context},
        ],
        options=_prompt_llm_options({
            "temperature": 0.5 + (attempt * 0.1),
            "num_predict": 4096 if config.llm_creative_drafting_enabled() else 3072,
            "num_ctx": 16384 if config.llm_creative_drafting_enabled() else 12288,
        }),  # Increase creativity on later retries
    )
    prompt = response["message"]["content"].strip()
    log.info("Retry prompt (attempt %d) for scene %d: %s...", attempt, scene["scene_number"], prompt[:80])
    return prompt
