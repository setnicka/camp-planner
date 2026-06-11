"""Service layer: non-trivial logic kept out of the views.

Views stay thin (parse request -> call a service -> flash/redirect or jsonify).

Transaction convention: a service that performs a complete operation (create /
save, including its audit row and any seeding) owns its transaction and commits;
pure readers (e.g. build_timeline) don't touch the session's commit state.
"""
