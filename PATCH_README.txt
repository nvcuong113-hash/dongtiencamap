QUANTS PRO V17.4 PATCH — V17.3 to V17.4
Build: SMF-P6-EVIDENCE-FUSION-20261031
Report schema: 2.6

Apply this Patch ZIP to the ROOT of the existing V17.3 repository.
The archive stores root-relative paths (app.py, quants/, tests/, .github/, docs/).
Do NOT place the contents inside a new dongtiencamap-main/ subfolder.

The Patch archive contains 29 changed/new files. Extract all patch files into the repository root and commit them together in one Git commit. Avoid intermediate commits where app.py imports modules that have not yet been committed. The uploaded Streamlit logs showed transient ModuleNotFoundError / KeyError: quants while the repository was incomplete during refresh.

No SQLite schema migration. Backup the production DB before deployment. Do not replace the production DB with a seed SQLite file.
