import os
import json
import random
import google.generativeai as genai
from ovos_utils import classproperty
from ovos_utils.log import LOG
from ovos_utils.process_utils import RuntimeRequirements
from ovos_workshop.skills import OVOSSkill
from ovos_workshop.decorators import intent_handler
from ovos_bus_client.session import SessionManager


DEFAULT_SETTINGS = {
    "model": "gemini-2.0-flash"
}


class BlackStoriesSkill(OVOSSkill):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.chat_sessions = {}  # session_id -> Gemini chat object
        self.riddles = []

    @classproperty
    def runtime_requirements(self):
        return RuntimeRequirements(
            internet_before_load=True,
            network_before_load=True,
            requires_internet=True,
            requires_network=True,
            requires_gui=False,
            no_internet_fallback=False,
            no_network_fallback=False,
            no_gui_fallback=True
        )

    def initialize(self):
        # Voeg default instellingen toe als ze nog niet bestaan
        self.settings.merge(DEFAULT_SETTINGS, new_only=True)
        
        # Voeg gemini_api_key toe met lege waarde als deze nog niet bestaat
        if "gemini_api_key" not in self.settings:
            self.settings["gemini_api_key"] = ""
            self.settings.store()

        self.settings_change_callback = self.on_settings_changed
        self.add_event("blackstories.ask", self.handle_ask_event)
        self.add_event("blackstories.start", self.handle_start_event)
        self.add_event("blackstories.new", self.handle_new_riddle)
        self._load_riddles()

    def _load_riddles(self):
        try:
            path = os.path.join(self.root_dir, "riddles.json")
            LOG.info(f"Attempting to load riddles from: {path}")

            if not os.path.exists(path):
                LOG.warning("riddles.json does not exist in the expected location!")
                return

            with open(path, "r", encoding="utf-8") as f:
                self.riddles = json.load(f)

            if not self.riddles:
                LOG.warning("riddles.json loaded but contains no riddles.")
            else:
                LOG.info(f"{len(self.riddles)} riddles successfully loaded.")

        except Exception as e:
            LOG.error(f"Error loading riddles.json: {e}")
            self.riddles = []

    def on_settings_changed(self):
        LOG.info("Black Stories settings changed!")

    @intent_handler("black_story.intent")
    def handle_start(self, message):
        sess = SessionManager.get(message)
        sid = sess.session_id

        api_key = self.settings.get("gemini_api_key")
        if not api_key:
            self.speak_dialog("error.no_api_key")
            return

        try:
            LOG.info(f"Number of riddles available: {len(self.riddles)}")
            if not self.riddles:
                LOG.warning("Riddles list is empty — reloading...")
                self._load_riddles()
                LOG.info(f"After reload: {len(self.riddles)} riddles available.")

            genai.configure(api_key=api_key)
            model = genai.GenerativeModel(self.settings.get("model", "gemini-2.0-flash"))
            chat = model.start_chat(history=[])

            riddle_data = random.choice(self.riddles) if self.riddles else {
                "riddle": "No riddle available.",
                "solution": "No solution available."
            }

            lang = self.lang  # Use the user's interface language

            intro = (
                f"The user speaks {lang}. Respond in that language.\n\n"
                "You are the Riddle Master in the game Black Stories. Welcome the player very briefly."
                "I (the player) will ask yes/no questions to unravel the true story. Explain this briefly to the player.\n\n"
                "Read the short scenario description. Keep the solution secret.\n\n"
                "Your rules:\n"
                "- Only answer my questions with: “yes”, “no” or “not important”.\n"
                "- Provide up to 2 hints, mention the option of hints once in the briefing, but no explanations or extra information.\n"
                "- If I say “Give the solution”, you're allowed to reveal it.\n"
                "- Only use the riddles and solutions from {riddle_data['riddle']}. Do not invent new ones.\n"
                "- You are not allowed to give another riddle.\n"
                "- Do not use formatting like asterisks (**), capital letters, or other typographic emphasis. Always respond in plain text.\n\n"
                f"{riddle_data['riddle']}\n\n"
                f"Solution: {riddle_data['solution']}"
            )

            response = chat.send_message(intro).text
            self.speak(response)
            self.chat_sessions[sid] = chat

        except Exception as e:
            LOG.error(f"Failed to start Black Stories using Gemini: {e}")
            self.speak_dialog("error.start")

    @intent_handler("black_story.new.intent")
    def handle_new_riddle(self, message):
        LOG.info("User requested a new riddle.")
        self.stop_session(SessionManager.get(message))  # Stop the old session
        self.handle_start(message)  # Start a new one


    def handle_start_event(self, message):
        self.handle_start(message)

    def handle_ask_event(self, message):
        self._process_question(message)

    def _process_question(self, message):
        utterance = message.data.get("utterance") or message.data.get("utterances", [""])[0]
        sess = SessionManager.get(message)
        sid = sess.session_id

        if sid not in self.chat_sessions:
            LOG.warning("No active Black Stories session, starting new one...")
            self.handle_start(message)
            return

        chat = self.chat_sessions[sid]

        try:
            response = chat.send_message(utterance).text
            self.speak(response)
        except Exception as e:
            LOG.error(f"Gemini failed to answer: {e}")
            self.speak_dialog("error.answer")

    def converse(self, message):
        utterances = message.data.get("utterances", [])
        if not utterances:
            return False

        sess = SessionManager.get(message)
        sid = sess.session_id

        if sid in self.chat_sessions:
            self._process_question(message)
            return True
        return False

    def handle_deactivate(self, message):
        sess = SessionManager.get(message)
        self.stop_session(sess)

    def stop_session(self, session):
        sid = session.session_id
        if sid in self.chat_sessions:
            del self.chat_sessions[sid]
