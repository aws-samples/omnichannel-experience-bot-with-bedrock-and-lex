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
    invocation_source = event.get("invocationSource")

    # If Lex could not determine user's intent, use LLM to identify the intent
    if intent["name"] == "FallbackIntent":
        intents = get_intents(
            bot_id,
            bot_version,
            locale_id,
        )

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

        if llm_identified_intent.upper() != "NOT SURE" and float(llm_confidence) >= 0.7:
            slots = get_slots(
                bot_id,
                bot_version,
                locale_id,
                llm_identified_intent,
            )
            next_slot = get_next_unfilled_slot(
                bot_id=bot_id,
                bot_version=bot_version,
                locale_id=locale_id,
                intent_name=llm_identified_intent,
                slots=slots
            )

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
    elif invocation_source == "DialogCodeHook":
        # Check if this is a slot miss by looking at the transcriptions
        transcriptions = event.get("transcriptions", [])
        is_slot_miss = False
        
        if transcriptions:
            resolved_context = transcriptions[0].get("resolvedContext", {})
            if resolved_context.get("intent") == "FallbackIntent":
                is_slot_miss = True
        
        if is_slot_miss:
            # Get the current slot being elicited from proposedNextState
            current_slot = event["proposedNextState"]["dialogAction"]["slotToElicit"]
            
            # Get slot type information to check if it's a custom slot
            slot_values = get_slot_values(
                bot_id=bot_id,
                bot_version=bot_version,
                locale_id=locale_id,
                intent=intent["name"],
                slot_type=current_slot
            )

            if slot_values:
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

                if llm_mapped_slot.upper() != "NOT SURE" and float(llm_confidence) >= 0.7:
                    slots = set_slot(
                        slots,
                        current_slot,
                        input_transcript,
                        llm_mapped_slot,
                    )

                    next_slot = get_next_unfilled_slot(
                        bot_id=bot_id,
                        bot_version=bot_version,
                        locale_id=locale_id,
                        intent_name=intent["name"],
                        slots=slots
                    )

                    if next_slot:
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

                else:
                    return {
                        "sessionState": {
                            "dialogAction": {
                                "type": "ElicitSlot",
                                "slotToElicit": current_slot,
                            },
                            "intent": {
                                "name": intent["name"],
                                "slots": slots,
                                "state": "InProgress",
                            },
                            "sessionAttributes": session_attributes,
                        }
                    }

    # For all other cases, delegate to Lex
    return {
        "sessionState": {
            "dialogAction": {"type": "Delegate"},
            "intent": {"name": intent["name"], "slots": slots, "state": "InProgress"},
            "sessionAttributes": session_attributes,
        }
    }
