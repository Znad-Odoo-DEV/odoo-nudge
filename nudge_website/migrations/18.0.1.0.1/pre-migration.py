"""Visitor questions are no more notes: a note that came from a visitor is deleted before the
selection value it used goes away, so none can be turned into another kind by that."""


def migrate(cr, version):
    cr.execute("SELECT 1 FROM information_schema.tables WHERE table_name = 'nudge_note'")
    if cr.fetchone():
        cr.execute("DELETE FROM nudge_note WHERE kind = 'unanswered'")
