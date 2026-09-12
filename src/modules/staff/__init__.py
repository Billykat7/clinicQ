"""Staff: who works at a clinic, and (Issue 22) how they are invited and switched off.

A staff member is a ``user`` row (Issue 15) whose clinic is a role assignment scoped to that site,
so this module has no model of its own: it reads staff **through the site guard**
(:func:`src.core.site_scope.staff_at_site`), which is what makes a Clinic A token answer 404 for a
Clinic B staff id (Issue 19).
"""
