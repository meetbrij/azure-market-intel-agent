import os

# Restrict checkpoint deserialisation to known-safe types (plus the graph's own
# state models, which LangGraph allowlists automatically), so a compromised
# database can't trigger code execution on load. LangGraph reads this when it
# is first imported, so it is set here, before any app module imports it.
# An explicit value in the environment wins.
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
