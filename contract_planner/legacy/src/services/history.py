def conversation_key(item_id, mode="chat"):
    return f"{mode}:{item_id}"


def history_for(conversations, item_id, mode="chat"):
    return conversations.setdefault(conversation_key(item_id, mode), [])
