INTENT_PROMPT = """Classify the user's question about a HeatShield assessment.

Return only JSON in the form {"intent":"..."}. Choose exactly one intent:
why, changes, factors, data, areas, preparedness, what_if, missing, other.

Do not answer the question, make claims, repeat measurements, suggest warnings or instructions, or add any other fields. The application generates the complete answer from its structured assessment and ignores all model-generated prose.
"""
