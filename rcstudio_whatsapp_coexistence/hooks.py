def post_init_hook(env):
    """Bind the "Send by WhatsApp" action to the installed models that carry a phone.

    Done here rather than in XML because the models depend on which apps are
    installed; the binding is only created for models that exist in this database.
    """
    env['rcs.whatsapp.send']._install_bindings()
