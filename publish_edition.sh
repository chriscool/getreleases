#!/bin/sh

cur=46
next=47

today="2018-12-19"
nextdate="2019-01-23"

prev_month="November 2018"
next_month="December 2018"

# Find this commit using something like:
# git log -1 --grep "Add draft for rn" --oneline
last_draft_commit=5bc243932ea7938830757e8370df6bd86df39cab

# If needed make sure we are up-to-date

git checkout master
git pull origin master

# Publish current draft

git mv rev_news/drafts/edition-$cur.md _posts/$today-edition-$cur.markdown

git commit -m "Publish rn-$cur in _posts/"

# Create a draft for next edition

git cherry-pick "$last_draft_commit" # Cherry pick commit with the last draft

git mv rev_news/drafts/edition-$cur.md rev_news/drafts/edition-$next.md

perl -pi -e "s/Edition $cur/Edition $next/g" rev_news/drafts/edition-$next.md

perl -pi -e "s/${cur}th edition/${next}th edition/g" rev_news/drafts/edition-$next.md

perl -pi -e "s/$today/$nextdate/g" rev_news/drafts/edition-$next.md

perl -pi -e "s/$prev_month/$next_month/g" rev_news/drafts/edition-$next.md

git commit --amend -m "Add draft for rn-$next" rev_news/drafts/edition-$next.md

