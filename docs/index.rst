.. Theme-aware logo for Read the Docs. The README's <picture> block keys off the OS
   ``prefers-color-scheme``, which does NOT track furo's independent light/dark toggle
   (toggling furo to light while the OS is in dark mode otherwise shows the dark,
   cream-wordmark logo on a white page). Furo's ``.only-light`` / ``.only-dark`` classes
   follow furo's ACTUAL theme, so render the logo with those here and skip the README's
   own <picture> in the include below (``:start-after: </p>`` drops the logo block, whose
   closing ``</p>`` is unique in the README).

.. image:: https://raw.githubusercontent.com/ErickShepherd/ncarnate/main/brand/ncarnate-lockup.png
   :class: only-light
   :width: 460
   :align: center
   :alt: ncarnate

.. image:: https://raw.githubusercontent.com/ErickShepherd/ncarnate/main/brand/ncarnate-lockup-dark.png
   :class: only-dark
   :width: 460
   :align: center
   :alt: ncarnate

.. include:: ../README.md
   :parser: myst_parser.sphinx_
   :start-after: </p>

.. toctree::
   :hidden:
   :maxdepth: 2

   Overview <self>
   API reference <api>
