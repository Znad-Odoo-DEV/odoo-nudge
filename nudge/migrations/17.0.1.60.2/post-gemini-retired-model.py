RETIRED = "gemini-2.5-flash-lite"
REPLACEMENT = "gemini-3.1-flash-lite"


def migrate(cr, version):
    """Google no longer gives gemini-2.5-flash-lite to new keys: every call to it fails. A provider that still
    names it, as its model or as its economy model, is moved to gemini-3.1-flash-lite."""
    for column in ("model_name", "economy_model_name"):
        cr.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'nudge_provider' AND column_name = %s",
                   (column,))
        if cr.fetchone():
            cr.execute(f"UPDATE nudge_provider SET {column} = %s WHERE {column} = %s", (REPLACEMENT, RETIRED))
