# OVERLAY: mobile-graphql — shared resolver helpers.


def get_user(info):
    """The authenticated User from the request context, or None."""
    user = getattr(info.context, "user", None)
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user
