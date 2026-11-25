# -*- coding: utf-8 -*-
from . import models


def _post_init_verify_existing_products(env):
    """Los productos que ya existían al instalar se dan por verificados para no
    frenar la operación; solo los productos nuevos esperan el visto bueno."""
    env.cr.execute(
        "UPDATE product_template SET verification_state = 'verified' "
        "WHERE verification_state IS NULL OR verification_state = 'pending'"
    )
