"""Smart Study, Free Practice, the review endpoint and the session summary.

Owner: engine + study. Built in phase 3. Reviews are saved with
style='smart' or style='free'; Free Practice never changes shelves.
"""

from flask import Blueprint

bp = Blueprint("study", __name__, url_prefix="/study")
