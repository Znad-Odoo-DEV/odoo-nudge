def migrate(cr, version):
    """Notes and lessons had no company: each takes the company of the person who proposed it, or
    else of the person who wrote it. Where that person is unknown (or is OdooBot) the note stays
    empty, told to all companies as before. A database with one company is left as it is."""
    cr.execute("SELECT count(*) FROM res_company")
    if cr.fetchone()[0] < 2:
        return
    cr.execute("""
        UPDATE nudge_note n
           SET company_id = u.company_id
          FROM res_users u
         WHERE n.company_id IS NULL
           AND u.id = COALESCE(n.proposed_by, n.create_uid)
           AND u.id > 1
    """)
