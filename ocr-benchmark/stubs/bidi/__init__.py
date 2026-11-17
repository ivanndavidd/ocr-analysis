"""Stand-in for python-bidi (LGPL, kept out of this environment). EasyOCR calls get_display only to reorder
right-to-left scripts (Arabic, Hebrew); Indonesian and English text passes through unchanged either way."""


def get_display(text, *args, **kwargs):
    return text
