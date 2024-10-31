import logging
import os
from dialog_utils import (
    get_intents,
    get_slots,
    get_slot_values,
    get_next_unfilled_slot,
    set_slot,
    invoke_bedrock,
    extract_tag_content,
)

# Logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Constants
MODEL_ID = os.environ.get("foundation_model")
logger.info(f"Using foundation model: {MODEL_ID}")


# Lambda handler
def lambda_handler(event, context):
    logger.info(f"# New Lex event: {event}")

    # User input
    input_transcript = event["inputTranscript"]

    # Bot information
    bot_id = event["bot"]["id"]
    bot_version = event["bot"]["version"]
    locale_id = event["bot"]["localeId"]

    # Proposed next state
    proposed_next_state = event.get("proposedNextState", None)

    # Session state
    session_state = event["sessionState"]
    intent = session_state["intent"]
    slots = session_state["intent"]["slots"]
    session_attributes = session_state["sessionAttributes"]
    elicited_slot_type = session_attributes.get("elicited_slot_type", None)

    # If Lex could not determine user's intent, use LLM to identify the intent based on user's utterance
    if intent["name"] == "FallbackIntent":
        intents = get_intents(
            bot_id,
            bot_version,
            locale_id,
        )

        # Invoke LLM hosted on Bedrock with slot assistance prompt
        with open("intent_identification_prompt.txt", "r") as file:
            intent_identification_prompt = file.read()
        llm_output = invoke_bedrock(
            intent_identification_prompt.format(
                intents=intents, utterance=input_transcript
            ),
            MODEL_ID,
        )

        llm_identified_intent = extract_tag_content(llm_output, "intent_output")
        llm_confidence = extract_tag_content(llm_output, "confidence_score")

        # If LLM was able to succesfully identify the intent of the user, update the intent and elicit the slot of the identified intent
        if llm_identified_intent.upper() != "NOT SURE" and float(llm_confidence) >= 0.7:
            slots = get_slots(
                bot_id,
                bot_version,
                locale_id,
                llm_identified_intent,
            )
            next_slot = get_next_unfilled_slot(slots)
            session_attributes["elicited_slot_type"] = next_slot

            return {
                "sessionState": {
                    "dialogAction": {
                        "type": "ElicitSlot",
                        "slotToElicit": next_slot,
                    },
                    "intent": {
                        "name": llm_identified_intent,
                        "slots": slots,
                        "state": "InProgress",
                    },
                    "sessionAttributes": session_attributes,
                }
            }

        # If LLM was unable to identify the intent, elicit for the intent again
        else:
            return {
                "sessionState": {
                    "dialogAction": {"type": "ElicitIntent"},
                    "intent": {"name": "FallbackIntent", "state": "Failed"},
                    "sessionAttributes": session_attributes,
                },
                "messages": [
                    {
                        "contentType": "PlainText",
                        "content": "I'm sorry, I didn't understand that. Could you please rephrase your request?",
                    }
                ],
            }

    # If user is elicited for slot, use LLM to assist mapping the utterance to slot type values
    elif elicited_slot_type:
        slot_values = get_slot_values(
            bot_id,
            bot_version,
            locale_id,
            intent["name"],
            elicited_slot_type,
        )

        # Invoke LLM hosted on Bedrock with slot assistance prompt
        with open("slot_assistance_prompt.txt", "r") as file:
            slot_assistance_prompt = file.read()
        llm_output = invoke_bedrock(
            slot_assistance_prompt.format(
                slot_values=slot_values, utterance=input_transcript
            ),
            MODEL_ID,
        )
        llm_mapped_slot = extract_tag_content(llm_output, "slot_output")
        llm_confidence = extract_tag_content(llm_output, "confidence_score")

        # If LLM was able to succesfully map the utterance to one of the slot type values, update slot type value
        if llm_mapped_slot.upper() != "NOT SURE" and float(llm_confidence) >= 0.7:
            slots = set_slot(
                slots,
                elicited_slot_type,
                input_transcript,
                llm_mapped_slot,
            )

            next_slot = get_next_unfilled_slot(slots)

            # If the intent has unfilled slot type, elicit that slot type next
            if next_slot:
                session_attributes["elicited_slot_type"] = next_slot
                return {
                    "sessionState": {
                        "dialogAction": {
                            "type": "ElicitSlot",
                            "slotToElicit": next_slot,
                        },
                        "intent": {
                            "name": intent["name"],
                            "slots": slots,
                            "state": "InProgress",
                        },
                        "sessionAttributes": session_attributes,
                    }
                }

            # If all slots are elicited for intent, move to ReadyForFulfillment
            else:
                return {
                    "sessionState": {
                        "dialogAction": {"type": "Delegate"},
                        "intent": {
                            "name": intent["name"],
                            "slots": slots,
                            "state": "ReadyForFulfillment",
                        },
                        "sessionAttributes": session_attributes,
                    }
                }

        # If LLM was unable to map the utterance to one of the slot type values, elicit for the same slot again
        else:
            return {
                "sessionState": {
                    "dialogAction": {
                        "type": "ElicitSlot",
                        "slotToElicit": elicited_slot_type,
                    },
                    "intent": {
                        "name": intent["name"],
                        "slots": slots,
                        "state": "InProgress",
                    },
                    "sessionAttributes": session_attributes,
                }
            }

    # Populate elicited slot type in session attributes
    elicited_slot_type = None
    if proposed_next_state:
        elicited_slot_type = proposed_next_state["dialogAction"]["slotToElicit"]
    session_attributes["elicited_slot_type"] = elicited_slot_type

    # Return the session state
    return {
        "sessionState": {
            "dialogAction": {"type": "Delegate"},
            "intent": {"name": intent["name"], "slots": slots, "state": "InProgress"},
            "sessionAttributes": session_attributes,
        }
    }
