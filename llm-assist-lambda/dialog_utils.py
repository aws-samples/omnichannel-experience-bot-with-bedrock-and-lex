import json
import boto3
import logging
import re

# Logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# boto3 clients
lex_client = boto3.client("lexv2-models")
bedrock_client = boto3.client("bedrock-runtime")


# Function to get all intents for given bot
def get_intents(bot_id, bot_version, locale_id):
    # Populate dictionary of intents for bot
    all_intents = {}
    next_token = None

    while True:
        # Prepare the base request parameters
        request_params = {
            "botId": bot_id,
            "botVersion": bot_version,
            "localeId": locale_id,
        }

        # Add nextToken if we have one
        if next_token:
            request_params["nextToken"] = next_token

        response = lex_client.list_intents(**request_params)

        # Process the current page of results
        for intent_summary in response["intentSummaries"]:
            intent_name = intent_summary["intentName"]
            intent_description = intent_summary["description"]
            all_intents[intent_name] = intent_description

        # Check if there are more results
        next_token = response.get("nextToken")
        if not next_token:
            break

    logger.info(f"All intents for bot are retrieved: {all_intents}")
    return all_intents


# Function to get all slot types for given intent
def get_slots(bot_id, bot_version, locale_id, intent):
    # Search for intent ID of given intent
    next_token = None
    intent_id = None

    # Keep searching through pages until we find the intent or run out of results
    while True:
        request_params = {
            "botId": bot_id,
            "botVersion": bot_version,
            "localeId": locale_id,
        }

        if next_token:
            request_params["nextToken"] = next_token

        response = lex_client.list_intents(**request_params)

        for intent_summary in response["intentSummaries"]:
            if intent_summary["intentName"] == intent:
                intent_id = intent_summary["intentId"]
                break

        if intent_id or not response.get("nextToken"):
            break

        next_token = response.get("nextToken")

    if not intent_id:
        logger.error(f"Intent '{intent}' not found")
        return None

    # Populate dictionary of slots for intent
    all_slots = {}
    next_token = None

    while True:
        request_params = {
            "botId": bot_id,
            "botVersion": bot_version,
            "localeId": locale_id,
            "intentId": intent_id,
        }

        if next_token:
            request_params["nextToken"] = next_token

        response = lex_client.list_slots(**request_params)

        for slot_summary in response["slotSummaries"]:
            slot_name = slot_summary["slotName"]
            all_slots[slot_name] = None

        next_token = response.get("nextToken")
        if not next_token:
            break

    logger.info(f"All slots for intent '{intent}' are retrived: {all_slots}")
    return all_slots


# Function to get all slot values within given slot type of intent
def get_slot_values(bot_id, bot_version, locale_id, intent, slot_type):
    # Search for intent ID of given intent
    next_token = None
    intent_id = None

    # Keep searching through pages until we find the intent or run out of results
    while True:
        request_params = {
            "botId": bot_id,
            "botVersion": bot_version,
            "localeId": locale_id,
        }

        if next_token:
            request_params["nextToken"] = next_token

        response = lex_client.list_intents(**request_params)

        for intent_summary in response["intentSummaries"]:
            if intent_summary["intentName"] == intent:
                intent_id = intent_summary["intentId"]
                break

        if intent_id or not response.get("nextToken"):
            break

        next_token = response.get("nextToken")

    if not intent_id:
        logger.error(f"Intent '{intent}' not found")
        return None

    # Search for slot ID of given slot type
    next_token = None
    slot_id = None

    while True:
        request_params = {
            "botId": bot_id,
            "botVersion": bot_version,
            "localeId": locale_id,
            "intentId": intent_id,
        }

        if next_token:
            request_params["nextToken"] = next_token

        response = lex_client.list_slots(**request_params)

        for slot_summary in response["slotSummaries"]:
            if slot_summary["slotName"] == slot_type:
                slot_id = slot_summary["slotTypeId"]
                break

        if slot_id or not response.get("nextToken"):
            break

        next_token = response.get("nextToken")

    if not slot_id:
        logger.error(f"Slot type '{slot_type}' not found")
        return None

    try:
        # Only try to describe slot type if it's a custom slot (10 char ID)
        if len(slot_id) <= 10:
            response = lex_client.describe_slot_type(
                botId=bot_id,
                botVersion=bot_version,
                localeId=locale_id,
                slotTypeId=slot_id,
            )
            all_slot_type_values = []

            # Note: describe_slot_type doesn't use pagination
            for slot_type_values in response["slotTypeValues"]:
                all_slot_type_values.append(slot_type_values["sampleValue"]["value"])

            logger.info(
                f"Slot type values for '{slot_type}' is retrieved: {all_slot_type_values}"
            )
            return all_slot_type_values
        else:
            logger.info(
                f"Slot type '{slot_type}' is a built-in type, skipping slot assistance"
            )
            return None

    except Exception as e:
        logger.error(f"Error describing slot type: {str(e)}")
        return None


# Function to set a new slot value within dictionary of slots
def set_slot(slots, slot_type, input_transcript, updated_slot):
    new_slot = {
        slot_type: {
            "shape": "Scalar",
            "value": {
                "originalValue": input_transcript,
                "resolvedValues": [updated_slot],
                "interpretedValue": updated_slot,
            },
        }
    }
    slots.update(new_slot)

    logger.info(
        f"Updated slot type '{slot_type}' with value from '{input_transcript}' to '{updated_slot}'"
    )
    return slots


# Function to get a slot that is not yet filled
def get_next_unfilled_slot(bot_id, bot_version, locale_id, intent_name, slots):
    # Get the intent ID first
    response = lex_client.list_intents(
        botId=bot_id,
        botVersion=bot_version,
        localeId=locale_id,
    )
    intent_id = None
    for intent_summary in response["intentSummaries"]:
        if intent_summary["intentName"] == intent_name:
            intent_id = intent_summary["intentId"]
            break

    if not intent_id:
        logger.error(f"Intent '{intent_name}' not found")
        return None

    # Get all slots to create a mapping of slotId to slotName
    slots_response = lex_client.list_slots(
        botId=bot_id, botVersion=bot_version, localeId=locale_id, intentId=intent_id
    )

    # Create a mapping of slot IDs to slot names
    slot_id_to_name = {
        slot["slotId"]: slot["slotName"] for slot in slots_response["slotSummaries"]
    }

    # Get the complete intent details to get slots in priority order
    intent_response = lex_client.describe_intent(
        botId=bot_id, botVersion=bot_version, localeId=locale_id, intentId=intent_id
    )

    # Get slots in their defined priority order
    slot_priorities = intent_response.get("slotPriorities", [])

    # Sort slot priorities by priority number
    sorted_priorities = sorted(slot_priorities, key=lambda x: x["priority"])

    # Log the slot order for debugging
    slot_order = [slot_id_to_name[priority["slotId"]] for priority in sorted_priorities]
    logger.info(f"Slots in priority order: {slot_order}")
    logger.info(f"Current slot values: {slots}")

    # Check each slot in priority order
    for priority in sorted_priorities:
        slot_name = slot_id_to_name[priority["slotId"]]
        if slot_name in slots and slots[slot_name] is None:
            logger.info(
                f"Will elicit slot type '{slot_name}' next based on priority order"
            )
            return slot_name

    logger.info("All slot(s) filled for this intent")
    return None


# Function to invoke LLM on Bedrock
def invoke_bedrock(prompt, model):
    logger.info(f"Incoming prompt: {prompt}")

    body = json.dumps(
        {
            "anthropic_version": "",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
    )
    response = bedrock_client.invoke_model(body=body, modelId=model)
    response_body = json.loads(response.get("body").read())
    answer = response_body.get("content")[0].get("text")

    logger.info(f"LLM response: {answer}")

    return answer


# Function to extract contents within specified xml tags
def extract_tag_content(content, tag_name):
    pattern = f"<{tag_name}>(.*?)</{tag_name}>"
    match = re.search(pattern, content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return None
