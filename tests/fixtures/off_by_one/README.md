# off_by_one

Bug: A loop boundary comparison uses `<` instead of `<=`, causing the last element to be skipped.

Bug type: off-by-one / loop boundary error
