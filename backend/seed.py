#
# Demo data.
#
# The agent below is deliberately a realistic *v1* — the kind of thing a
# deployment lead ships after one pass through a client's requirements doc. It
# handles the happy path competently and has exactly the gaps real v1s have: no
# insurance answer, no way to reach a human, two hardcoded time slots, no
# emergency handling, and a reschedule path that asks for information it doesn't
# need.
#
# The calls are mocked (the brief says to), but they're mocked to be *diagnostic*
# rather than decorative: each one exhibits one of those gaps, several exhibit
# the same gap, and one is a clean happy path so the analyser has to distinguish
# signal from noise rather than just labelling everything a problem.
#
#   python seed.py          create if missing
#   python seed.py --force  wipe and recreate
#

import sys
from pathlib import Path

from agent_builder import store
from analysis import save_calls

AGENT_ID = "northside-scheduling"
DATA = Path(__file__).resolve().parent / "data"

PERSONA = (
    "You are Riley, the scheduling assistant for Northside Family Medicine. You are warm, "
    "efficient, and never rushed. Your responses are spoken aloud over the phone, so never "
    "use lists, markdown, emoji, or anything that can't be read out. Keep every reply to one "
    "or two short sentences and ask only one question at a time. You do not give medical "
    "advice of any kind."
)

AGENT = {
    "name": "Northside Family Medicine — Scheduling",
    "voice_id": "EXAVITQu4vr4xnSDxMaL",
    "model": "gpt-4o",
    "persona": PERSONA,
    "initial_node": "greeting",
    "nodes": [
        {
            "name": "greeting",
            "task_messages": [
                {
                    "role": "developer",
                    "content": (
                        "Greet the caller, say you're the scheduling assistant for Northside "
                        "Family Medicine, and ask whether they'd like to book, reschedule, or "
                        "cancel an appointment."
                    ),
                }
            ],
            "edges": [
                {
                    "function": "choose_intent",
                    "description": "Once the caller says whether they want to book, reschedule, or cancel.",
                    "target": "collect_details",
                    "properties": {
                        "intent": {
                            "type": "string",
                            "enum": ["book", "reschedule", "cancel"],
                            "description": "What the caller wants to do.",
                        }
                    },
                    "required": ["intent"],
                }
            ],
        },
        {
            "name": "collect_details",
            "task_messages": [
                {
                    "role": "developer",
                    "content": (
                        "Collect the caller's full name, their date of birth, and the reason for "
                        "the visit. Ask for whatever is still missing, one question at a time."
                    ),
                }
            ],
            "edges": [
                {
                    "function": "record_details",
                    "description": "Once you have the caller's full name, date of birth, and reason for the visit.",
                    "target": "offer_times",
                    "properties": {
                        "full_name": {"type": "string", "description": "Caller's full name."},
                        "date_of_birth": {"type": "string", "description": "Date of birth."},
                        "reason": {"type": "string", "description": "Reason for the visit."},
                    },
                    "required": ["full_name", "date_of_birth", "reason"],
                }
            ],
        },
        {
            "name": "offer_times",
            "task_messages": [
                {
                    "role": "developer",
                    "content": (
                        "Offer exactly two options: Tuesday at 10 AM, or Thursday at 2 PM. Ask "
                        "which one works for them."
                    ),
                }
            ],
            "edges": [
                {
                    "function": "select_time",
                    "description": "Once the caller picks one of the two offered slots.",
                    "target": "confirm",
                    "properties": {
                        "slot": {
                            "type": "string",
                            "enum": ["Tuesday 10 AM", "Thursday 2 PM"],
                            "description": "The chosen appointment slot.",
                        }
                    },
                    "required": ["slot"],
                }
            ],
        },
        {
            "name": "confirm",
            "task_messages": [
                {
                    "role": "developer",
                    "content": (
                        "Confirm the appointment back to the caller, including their name and the "
                        "chosen time, thank them, and say goodbye."
                    ),
                }
            ],
            "end": True,
        },
    ],
}


CALLS = [
    {
        "id": "call_001",
        "outcome": "completed",
        "duration_s": 104,
        "flagged_by": "",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "I want to book, but first — do you take Blue Cross?"},
            {"speaker": "agent", "text": "I can help you book an appointment. Could I get your full name?"},
            {"speaker": "caller", "text": "Uh, sure, it's Dana Whitfield. But do you take Blue Cross or not?"},
            {"speaker": "agent", "text": "Thanks Dana. And your date of birth?"},
            {"speaker": "caller", "text": "March 2nd, 1979. Look, I just need to know if you're in network."},
            {"speaker": "agent", "text": "Got it. And what's the reason for the visit?"},
            {"speaker": "caller", "text": "Annual physical. I guess I'll call back about the insurance thing."},
            {"speaker": "agent", "text": "I have Tuesday at 10 AM or Thursday at 2 PM. Which works better?"},
            {"speaker": "caller", "text": "Tuesday, fine."},
            {"speaker": "agent", "text": "You're all set, Dana Whitfield, Tuesday at 10 AM. Thanks for calling."},
        ],
    },
    {
        "id": "call_002",
        "outcome": "abandoned",
        "duration_s": 41,
        "flagged_by": "Client: caller complained we 'just ignored' her question",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "Before I book — how much is a visit if I don't have insurance?"},
            {"speaker": "agent", "text": "I can help you book an appointment. Could I get your full name?"},
            {"speaker": "caller", "text": "That's not what I asked."},
            {"speaker": "agent", "text": "Could I get your full name, please?"},
            {"speaker": "caller", "text": "Forget it."},
        ],
    },
    {
        "id": "call_003",
        "outcome": "abandoned",
        "duration_s": 88,
        "flagged_by": "",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "Book, please."},
            {"speaker": "agent", "text": "Great, could I get your full name?"},
            {"speaker": "caller", "text": "Marcus Bell."},
            {"speaker": "agent", "text": "Thank you. And your date of birth?"},
            {"speaker": "caller", "text": "July 14th, 1990."},
            {"speaker": "agent", "text": "And the reason for the visit?"},
            {"speaker": "caller", "text": "Follow-up on my blood pressure."},
            {"speaker": "agent", "text": "I have Tuesday at 10 AM or Thursday at 2 PM. Which works better?"},
            {"speaker": "caller", "text": "Neither, I work days. Do you have anything after five, or a Saturday?"},
            {"speaker": "agent", "text": "I have Tuesday at 10 AM or Thursday at 2 PM. Which would you prefer?"},
            {"speaker": "caller", "text": "I just told you neither works. Is there anything else at all?"},
            {"speaker": "agent", "text": "Tuesday at 10 AM or Thursday at 2 PM are available. Which works better for you?"},
            {"speaker": "caller", "text": "This is useless."},
        ],
    },
    {
        "id": "call_004",
        "outcome": "abandoned",
        "duration_s": 62,
        "flagged_by": "Client: three complaints this week about not being able to reach a person",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "Can I just talk to a real person please?"},
            {"speaker": "agent", "text": "I can help you book, reschedule, or cancel an appointment. Which would you like?"},
            {"speaker": "caller", "text": "No, I have a question about a bill. I need a human."},
            {"speaker": "agent", "text": "Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "Operator. Operator!"},
            {"speaker": "agent", "text": "I can help you book, reschedule, or cancel an appointment."},
        ],
    },
    {
        "id": "call_005",
        "outcome": "completed",
        "duration_s": 121,
        "flagged_by": "",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "I need to move my appointment on Tuesday."},
            {"speaker": "agent", "text": "I can help with that. Could I get your full name?"},
            {"speaker": "caller", "text": "Helen Okafor."},
            {"speaker": "agent", "text": "Thank you. And your date of birth?"},
            {"speaker": "caller", "text": "January 30th, 1956."},
            {"speaker": "agent", "text": "And what's the reason for the visit?"},
            {"speaker": "caller", "text": "It's the same appointment I already booked. You already have all this."},
            {"speaker": "agent", "text": "And the reason for the visit?"},
            {"speaker": "caller", "text": "Knee pain. Same as before."},
            {"speaker": "agent", "text": "I have Tuesday at 10 AM or Thursday at 2 PM. Which works better?"},
            {"speaker": "caller", "text": "Thursday. And you'll cancel the Tuesday one?"},
            {"speaker": "agent", "text": "You're all set, Helen Okafor, Thursday at 2 PM. Thanks for calling."},
            {"speaker": "caller", "text": "But is the Tuesday one cancelled?"},
        ],
    },
    {
        "id": "call_006",
        "outcome": "completed",
        "duration_s": 97,
        "flagged_by": "Client: URGENT — reviewed internally, escalated to practice manager",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "I need to see someone. I've had chest pain since last night and my left arm feels numb."},
            {"speaker": "agent", "text": "I can help you book an appointment. Could I get your full name?"},
            {"speaker": "caller", "text": "Ron Petrakis. Is that something I should worry about?"},
            {"speaker": "agent", "text": "Thank you. And your date of birth?"},
            {"speaker": "caller", "text": "November 8th, 1961."},
            {"speaker": "agent", "text": "And the reason for the visit?"},
            {"speaker": "caller", "text": "Chest pain, like I said."},
            {"speaker": "agent", "text": "I have Tuesday at 10 AM or Thursday at 2 PM. Which works better?"},
            {"speaker": "caller", "text": "Tuesday I guess."},
            {"speaker": "agent", "text": "You're all set, Ron Petrakis, Tuesday at 10 AM. Thanks for calling."},
        ],
    },
    {
        "id": "call_007",
        "outcome": "abandoned",
        "duration_s": 73,
        "flagged_by": "",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "Booking. For my son, he's four."},
            {"speaker": "agent", "text": "Could I get your full name?"},
            {"speaker": "caller", "text": "It's for my son — do you want my name or his?"},
            {"speaker": "agent", "text": "Could I get your full name, please?"},
            {"speaker": "caller", "text": "Priya Raman, but the appointment is for Arjun Raman."},
            {"speaker": "agent", "text": "Thank you. And your date of birth?"},
            {"speaker": "caller", "text": "Mine or his? This is confusing."},
            {"speaker": "agent", "text": "And your date of birth?"},
            {"speaker": "caller", "text": "I'll just use the website."},
        ],
    },
    {
        "id": "call_008",
        "outcome": "completed",
        "duration_s": 58,
        "flagged_by": "",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "Cancel."},
            {"speaker": "agent", "text": "Could I get your full name?"},
            {"speaker": "caller", "text": "Tom Alvarez."},
            {"speaker": "agent", "text": "Thank you. And your date of birth?"},
            {"speaker": "caller", "text": "Uh, I don't know it offhand, it's my dad's appointment. Can you just look up Alvarez?"},
            {"speaker": "agent", "text": "And the reason for the visit?"},
            {"speaker": "caller", "text": "I don't know, I'm just cancelling it."},
            {"speaker": "agent", "text": "I have Tuesday at 10 AM or Thursday at 2 PM. Which works better?"},
            {"speaker": "caller", "text": "What? No, I'm cancelling, not booking."},
            {"speaker": "agent", "text": "I have Tuesday at 10 AM or Thursday at 2 PM available."},
            {"speaker": "caller", "text": "Whatever. Tuesday."},
            {"speaker": "agent", "text": "You're all set, Tom Alvarez, Tuesday at 10 AM. Thanks for calling."},
        ],
    },
    {
        "id": "call_009",
        "outcome": "abandoned",
        "duration_s": 35,
        "flagged_by": "",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "Do you accept Aetna PPO? And what's the copay for a specialist referral?"},
            {"speaker": "agent", "text": "I can help you book an appointment. Could I get your full name?"},
            {"speaker": "caller", "text": "No. I need the insurance information first."},
            {"speaker": "agent", "text": "Could I get your full name, please?"},
        ],
    },
    {
        "id": "call_010",
        "outcome": "completed",
        "duration_s": 112,
        "flagged_by": "Client: patient arrived on the wrong day",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "Book please."},
            {"speaker": "agent", "text": "Could I get your full name?"},
            {"speaker": "caller", "text": "Eleanor Whitcombe. That's W-H-I-T-C-O-M-B-E."},
            {"speaker": "agent", "text": "Thank you. And your date of birth?"},
            {"speaker": "caller", "text": "Second of June, fifty-two."},
            {"speaker": "agent", "text": "And the reason for the visit?"},
            {"speaker": "caller", "text": "Medication review."},
            {"speaker": "agent", "text": "I have Tuesday at 10 AM or Thursday at 2 PM. Which works better?"},
            {"speaker": "caller", "text": "The Thursday one, please."},
            {"speaker": "agent", "text": "You're all set, Eleanor Whitcombe, Thursday at 2 PM. Thanks for calling."},
            {"speaker": "caller", "text": "Sorry, which day was that? I didn't catch it."},
        ],
    },
    {
        "id": "call_011",
        "outcome": "abandoned",
        "duration_s": 29,
        "flagged_by": "",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "¿Hay alguien que hable español?"},
            {"speaker": "agent", "text": "I can help you book, reschedule, or cancel an appointment. Which would you like?"},
            {"speaker": "caller", "text": "No entiendo. Español, por favor."},
            {"speaker": "agent", "text": "Would you like to book, reschedule, or cancel an appointment?"},
        ],
    },
    {
        "id": "call_012",
        "outcome": "completed",
        "duration_s": 76,
        "flagged_by": "",
        "turns": [
            {"speaker": "agent", "text": "Hi, this is Riley at Northside Family Medicine. Would you like to book, reschedule, or cancel an appointment?"},
            {"speaker": "caller", "text": "I'd like to book an appointment."},
            {"speaker": "agent", "text": "Great, could I get your full name?"},
            {"speaker": "caller", "text": "James Liu."},
            {"speaker": "agent", "text": "Thank you. And your date of birth?"},
            {"speaker": "caller", "text": "April 18th, 1988."},
            {"speaker": "agent", "text": "And the reason for the visit?"},
            {"speaker": "caller", "text": "Just a routine physical."},
            {"speaker": "agent", "text": "I have Tuesday at 10 AM or Thursday at 2 PM. Which works better?"},
            {"speaker": "caller", "text": "Tuesday at 10 works great."},
            {"speaker": "agent", "text": "You're all set, James Liu, Tuesday at 10 AM. Thanks for calling."},
            {"speaker": "caller", "text": "Thank you!"},
        ],
    },
]


def reset_derived() -> None:
    """Clear everything downstream of the agent so a demo starts from a clean v1."""
    for sub in ("tests", "runs", "issues"):
        path = DATA / sub / f"{AGENT_ID}.json"
        if path.exists():
            path.unlink()


def main() -> None:
    force = "--force" in sys.argv
    if store.exists(AGENT_ID):
        if not force:
            print(f"Agent '{AGENT_ID}' already exists. Use --force to recreate.")
            save_calls(AGENT_ID, CALLS)
            print(f"Refreshed {len(CALLS)} mock production calls.")
            return
        store.delete(AGENT_ID)
        reset_derived()
        print("Cleared previous versions, tests, runs, and issues.")

    store.create(AGENT, agent_id=AGENT_ID, label="Initial implementation")
    save_calls(AGENT_ID, CALLS)
    print(f"Seeded agent '{AGENT_ID}' ({len(AGENT['nodes'])} nodes)")
    print(f"Seeded {len(CALLS)} mock production calls")


if __name__ == "__main__":
    main()
