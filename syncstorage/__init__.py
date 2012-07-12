# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this file,
# You can obtain one at http://mozilla.org/MPL/2.0/.

from mozsvc.config import get_configurator
from mozsvc.metrics import load_metlog_client
from syncstorage.controller import StorageController


def includeme(config):
    # Ensure that we have metlog loaded and ready for use as early as possible.
    load_metlog_client(config)
    # Include dependencies from other packages.
    config.include("cornice")
    config.include("mozsvc")
    config.include("mozsvc.user")
    # Add in the stuff we define ourselves.
    config.add_renderer("newlines", "syncstorage.views:NewlinesRenderer")
    config.include("syncstorage.tweens")
    config.include("syncstorage.storage")
    config.scan("syncstorage.views")
    # Create a "controller" object.  This is a vestiage of the
    # pre-pyramid codebase and will probably go away in the future.
    config.registry["syncstorage.controller"] = StorageController(config)


def main(global_config, **settings):
    config = get_configurator(global_config, **settings)
    # Ensure that we have metlog loaded and ready for use as early as possible.
    load_metlog_client(config)
    config.begin()
    try:
        config.include(includeme)
    finally:
        config.end()
    return config.make_wsgi_app()
