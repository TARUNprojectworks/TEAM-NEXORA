"""Weak Spot Fixer: explain the weak topic, give links, add 3 practice cards.

Owner: AI. Built in phase 5. Fixer cards go into the student's
"Weak spot practice" deck (Deck.is_weak_spot) with importance='high'.
At most one Fixer per study session.
"""

from flask import Blueprint

bp = Blueprint("fixer", __name__, url_prefix="/fixer")
