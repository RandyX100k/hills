# -*- coding: utf-8 -*-
"""Excepciones puntuales al candado de Product Price Lock Pro (Odonity).

No se edita el módulo de Odonity (su licencia lo prohíbe). Se envuelven sus
métodos create/write para saltar SOLO su chequeo de Price Manager cuando una
regla registrada lo permite; en ese caso se llama directo al método que sigue
a Odonity en la cadena, así que ningún otro módulo se salta.

Este archivo es idéntico en purchase_receipt_price_unlock y
product_verification_price_lock: las reglas se guardan en la clase de Odonity,
así que el envoltorio se instala una sola vez aunque lo importen los dos.
"""
from odoo import api

_RULES_ATTR = '_rt_price_lock_bypass_rules'


def _rules(cls):
    rules = cls.__dict__.get(_RULES_ATTR)
    if rules is None:
        rules = {}
        setattr(cls, _RULES_ATTR, rules)
        _wrap(cls, rules)
    return rules


def _wrap(cls, rules):
    original_write = cls.__dict__['write']

    def write(self, vals):
        if any(rule(self, vals) for rule in rules.get('write', {}).values()):
            return super(cls, self).write(vals)
        return original_write(self, vals)

    cls.write = write

    original_create = cls.__dict__.get('create')
    if original_create:
        @api.model_create_multi
        def create(self, vals_list):
            for rule in rules.get('create', {}).values():
                context = rule(self, vals_list)
                if context:
                    records = super(cls, self.with_context(**context)).create(vals_list)
                    return records.with_env(self.env)
            return original_create(self, vals_list)

        cls.create = create


def register(cls, method, name, rule):
    """write: rule(records, vals) -> bool. create: rule(model, vals_list) -> dict de contexto o None."""
    _rules(cls).setdefault(method, {})[name] = rule
