"""JSON schemas the model reads to decide which tool to call.

The model never sees email_tools.py. It sees only what is written here, so
every description below is doing real work: it is the sole thing standing
between a correct tool choice and a wrong one.
"""

SEARCH_EMAILS_SCHEMA = {
    "name": "search_emails",
    # The description answers three questions: what it does, what comes back,
    # and when to reach for something else instead. That last part is what
    # stops the model calling this when it should call get_email.
    "description": (
        "Search the user's Gmail inbox and return summaries of matching "
        "messages, newest first. Each result contains only email_id, subject, "
        "sender and date - it does NOT contain the message body. To read the "
        "contents of a message, call this first to find its email_id, then "
        "call get_email with that id. All filters are optional and are "
        "combined with AND; calling with no filters returns the most recent "
        "messages in the inbox. Searches the inbox by default, but switches "
        "to all mail when a label filter is used, so archived messages are "
        "still found."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                # Saying where it searches prevents the model assuming it is
                # subject-only and adding redundant filters to compensate.
                "description": (
                    "Free text to match anywhere in the message, including "
                    "subject and body. Use a distinctive word or short "
                    "phrase, not a full sentence."
                ),
            },
            "sender": {
                "type": "string",
                # Spelling out that partial matches work stops the model
                # refusing when the user only gave a first name.
                "description": (
                    "Match the sender. A partial string works - 'peter' "
                    "matches peter.otieno@example.com. Use this when looking "
                    "for mail the user RECEIVED from someone."
                ),
            },
            "recipient": {
                "type": "string",
                # sender vs recipient is the distinction that decides whether
                # a question is about mail received or mail the user sent.
                "description": (
                    "Match the recipient. Use this when looking for mail the "
                    "user SENT to someone - for example to check whether "
                    "they already replied or already sent a document. "
                    "Partial strings work."
                ),
            },
            "after": {
                "type": "string",
                # Without the explicit format the model will send 'last
                # Tuesday' and the date parser will raise.
                "description": (
                    "Only return messages sent on or after this date, in "
                    "YYYY-MM-DD format. Resolve relative dates such as "
                    "'yesterday' or 'last week' to an actual date before "
                    "calling. To search a single day, set 'after' to that "
                    "day and 'before' to the following day."
                ),
            },
            "before": {
                "type": "string",
                "description": (
                    "Only return messages sent before this date, in "
                    "YYYY-MM-DD format. This bound is exclusive."
                ),
            },
            "unread_only": {
                "type": "boolean",
                # The warning matters: 'unread' and 'not replied to' are
                # different things, and conflating them was a graded error.
                "description": (
                    "When true, return only unread messages. Default false. "
                    "Do NOT use this as a proxy for 'not yet replied to' - "
                    "a message can be read and still unanswered."
                ),
            },
            "label": {
                "type": "string",
                # Naming list_labels here is what stops the model inventing
                # a label that does not exist on the account.
                "description": (
                    "Match messages carrying this Gmail label. The label "
                    "must already exist - call list_labels first to see what "
                    "is available rather than guessing a name. Label matching "
                    "is exact, including capitalisation."
                ),
            },
            "has_attachment": {
                "type": "boolean",
                # The caution mirrors the unread_only one: only set a filter
                # the user actually asked for.
                "description": (
                    "When true, return only messages that carry an "
                    "attachment. Default false. Only set this when the user "
                    "has actually mentioned an attachment or a file - "
                    "otherwise it may exclude the message they meant."
                ),
            },
            "limit": {
                "type": "integer",
                "description": (
                    "Maximum number of results to return. Default 20, "
                    "maximum 50. The inbox is large, so keep this small "
                    "unless the user asks for an exhaustive list."
                ),
            },
        },
        # Nothing is required: every filter is optional in the Python, and
        # forcing one would stop the model answering 'what's new in my inbox'.
        "required": [],
    },
}


GET_EMAIL_SCHEMA = {
    "name": "get_email",
    # Three jobs for this description: say what it adds over search_emails,
    # state where the id has to come from, and close off the attachment
    # capability gap before the model promises the user something it cannot do.
    "description": (
        "Retrieve one email in full by its id, including the message body, "
        "the recipient, and the filenames of any attachments. Use this after "
        "search_emails has found the message the user means - search returns "
        "ids and subjects only, so this is the only way to read what a "
        "message actually says. Attachments are listed by filename only and "
        "cannot be downloaded, opened or forwarded; if the user needs the "
        "contents of an attachment, tell them it is not available rather "
        "than guessing at it."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "email_id": {
                "type": "string",
                # The explicit ban on invention is the important sentence.
                # A fabricated id either errors or silently returns the
                # wrong message, and the second failure is worse.
                "description": (
                    "The id of the message to retrieve, exactly as returned "
                    "in the email_id field of a previous search_emails "
                    "result. Never invent, guess or modify an id - if you do "
                    "not have one, call search_emails first."
                ),
            },
        },
        # Unlike search, this one cannot run without its argument.
        "required": ["email_id"],
    },
}


LIST_LABELS_SCHEMA = {
    "name": "list_labels",
    # A lookup tool exists so the model can check reality instead of
    # assuming. Saying so explicitly is what makes it get used.
    "description": (
        "List the Gmail labels that exist on this account. Takes no "
        "arguments. Call this before using the label filter in "
        "search_emails, so that the label name is one that actually exists "
        "rather than a guess. Also useful when the user refers to a folder "
        "or category by an approximate name and you need to find the real "
        "one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}




SUMMARISE_SENDERS_SCHEMA = {
    "name": "summarise_senders",
    # Counts, not messages. Asked "who fills my inbox", searching and
    # listing would return twenty subject lines from one sender; this
    # returns every sender with totals.
    "description": (
        "Group the inbox by sender and return counts, newest first. Returns "
        "each sender's address, display name, how many messages they sent, "
        "and a couple of example subjects. Use this for questions about "
        "volume or clutter - who sends the most, what is filling the inbox - "
        "rather than search_emails, which returns individual messages. "
        "Addresses on the same domain are flagged as related, so a sender "
        "using two subdomains is visible as one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Optional text filter, same as search_emails.",
            },
            "label": {
                "type": "string",
                "description": (
                    "Limit to one Gmail label. Call list_labels first to "
                    "get a name that exists."
                ),
            },
            "after": {
                "type": "string",
                "description": "YYYY-MM-DD. Only count mail sent on or after this date.",
            },
            "before": {
                "type": "string",
                "description": "YYYY-MM-DD, exclusive.",
            },
        },
        "required": [],
    },
}


SUMMARISE_SUBJECTS_SCHEMA = {
    "name": "summarise_subjects",
    # One sender is often several kinds of mail. A bank sends statements,
    # promotions and action-required notices from one address.
    "description": (
        "Break one sender's mail down by subject pattern. Returns the "
        "distinct kinds of mail that sender sends, with a count and a real "
        "example of each, rather than the individual messages - a sender "
        "with 500 emails typically has under 25 patterns. Use this after "
        "summarise_senders when the question is what KIND of mail someone "
        "sends, or whether all of it is the same thing."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "sender": {
                "type": "string",
                "description": (
                    "The sender to break down, as an address or partial "
                    "match. Take it from a summarise_senders result rather "
                    "than guessing."
                ),
            },
            "after": {
                "type": "string",
                "description": "YYYY-MM-DD. Optional lower bound.",
            },
            "before": {
                "type": "string",
                "description": "YYYY-MM-DD, exclusive. Optional upper bound.",
            },
        },
        "required": ["sender"],
    },
}


READ_CATEGORIES_SCHEMA = {
    "name": "read_categories",
    # Cached, so it is cheap and - more importantly - it is the same
    # grouping the approval step acts on.
    "description": (
        "Read the saved breakdown of the inbox: every significant sender "
        "with their mail sorted into named categories, and whether each "
        "category is something the user would want to see or is bulk. This "
        "is pre-computed, so it is fast and costs nothing. Use it for any "
        "question about what is cluttering the inbox or what could be "
        "cleared. Returns null if the breakdown has not been built yet, in "
        "which case tell the user to run 'python categorise_all.py build' - "
        "you cannot build it yourself."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}


TOOLS = [
    SEARCH_EMAILS_SCHEMA,
    GET_EMAIL_SCHEMA,
    LIST_LABELS_SCHEMA,
    SUMMARISE_SENDERS_SCHEMA,
    SUMMARISE_SUBJECTS_SCHEMA,
    READ_CATEGORIES_SCHEMA,
]
