#!/bin/sh
#
# This script should be used from the root directory of the
# git.github.io repository. It will publish an edition using an
# existing draft (this will create a commit) and create a new empty
# draft for the following edition (this will create another commit).
#
# Before running this script you have to decide the next publication
# date (set 'nextdate' below) and find the commit that added the
# previous draft (set 'last_draft_commit' below). After that you can
# run the script using for example:
#
# ../getreleases/publish_edition.sh
#

# TODO: Automate finding next publication date
nextdate="2019-03-20"

# TODO: Find the commit below using something like:
# TODO: git log -1 --grep "Add draft for rn" --oneline
# TODO: or better use a template
last_draft_commit=c15709332e11f63b30d59b2b392929aaca648b53


repo_url="https://github.com/git/git.github.io.git"
known_good_commit="5bc243932ea7938830757e8370df6bd86df39cab"
src_dir="rev_news/drafts"
dst_dir="_posts"

die() {
    printf >&2 "FATAL: %s\n" "$@"
    exit 1
}

# Basic checks

type git >/dev/null || die "git not found" "we need git"

git show "$known_good_commit" >/dev/null 2>&1 ||
	die "$known_good_commit not found" \
	    "we need to be in a repo cloned from $repo_url"

cur_branch=$(git rev-parse --abbrev-ref HEAD)
test "$cur_branch" = "master" || die "please switch to the 'master' branch"

test -d "$src_dir" || die "no source '$src_dir' directory"

test -d "$dst_dir" || die "no destination '$dst_dir' directory"

nb_ed=$(ls "$src_dir"/edition-*.md | wc -l)

test "$nb_ed" -eq 0 && die "no 'edition-*.md' file in '$src_dir' directory"

test "$nb_ed" -gt 1 && die "more than one 'edition-*.md' file in '$src_dir' directory"


# Find info we need

edition=$(ls "$src_dir"/edition-*.md)

cur=$(expr "$edition" : "rev_news/drafts/edition-\([0-9]\+\).md")

test -n "$cur" || die "'$edition' should contain a number"

next=$(expr "$cur" + 1)

today=$(date "+%Y-%m-%d")

# Each edition covers the previous month
next_month=$(LANG=C date "+%B %Y")
prev_month=$(LANG=C date --date="$today - 1 month" "+%B %Y")

# Publish current draft

git mv "$src_dir"/edition-$cur.md "$dst_dir"/$today-edition-$cur.markdown

git commit -m "Publish rn-$cur in $dst_dir/"

# Create a draft for next edition

git cherry-pick "$last_draft_commit"

cur_ed="$src_dir/edition-$cur.md"
next_ed="$src_dir/edition-$next.md"

test -f "$cur_ed" || die "cannot find '$cur_ed'"

git mv "$cur_ed" "$next_ed" ||
	die "failed to 'git mv $cur_ed $next_ed'"

add_order_suffix() {
perl -e '
	my $nb = $ARGV[0];
	if ($nb =~ m/[02-9]1$/) {
		print $nb . "st\n";
	} elsif ($nb =~ m/[02-9]2$/) {
		print $nb . "nd\n";
	} elsif ($nb =~ m/[02-9]3$/) {
		print $nb . "rd\n";
	} else {
		print $nb . "th\n";
	}
' "$1"
}

cur_ord=$(add_order_suffix "$cur")
next_ord=$(add_order_suffix "$next")

perl -pi -e "

	s/Edition $cur/Edition $next/g;
	s/${cur_ord} edition/${next_ord} edition/g;
	s/$today/$nextdate/g;
	s/$prev_month/$next_month/g;

" "$next_ed"

git commit --amend -m "Add draft for rn-$next" "$cur_ed" "$next_ed"

