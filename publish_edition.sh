#!/bin/sh
#
# This script should be used from the root directory of the
# git.github.io repository. It will publish an edition using an
# existing draft (this will create a commit) and create a new empty
# draft for the following edition (this will create another commit).
#
# This script can be run like this:
#
# $ ../getreleases/publish_edition.sh [<next date> [<cur date>]]
#
# where <next date> is the publication date for the following edition
# and <cur date> is the publication date for the current edition.
#
# If <cur date> is not provided, the publication date for the current
# edition made from the current draft is today.
#
# If <next date> is not provided to the script, it will compute it by
# adding one month to today's date and then finding the following
# wednesday.
#

# Global environment

export LANG=C

# Repo related constants

repo_name="git/git.github.io"
repo_url="https://github.com/$repo_name.git"
known_good_commit="5bc243932ea7938830757e8370df6bd86df39cab"
src_dir="rev_news/drafts"
dst_dir="_posts"
main_branch="master"


# Useful infos from environments

today=$(date "+%F")
basedir=$(dirname "$0")


# Helper functions

die() {
    printf >&2 "FATAL: %s\n" "$@"
    exit 1
}

add_order_suffix() {
perl -e '
	my $nb = $ARGV[0];
	if ($nb =~ m/^(.*[02-9\D])?1$/) {
		print $nb . "st\n";
	} elsif ($nb =~ m/^(.*[02-9\D])?2$/) {
		print $nb . "nd\n";
	} elsif ($nb =~ m/^(.*[02-9\D])?3$/) {
		print $nb . "rd\n";
	} else {
		print $nb . "th\n";
	}
' "$1"
}


# Process arguments

arg_next="$1"
arg_cur="$2"

test "$#" -le 2 ||
	die "too many arguments" "Usage: $0 [<next date> [<cur date>]]"


# Compute nextdate, the publication date for the next edition

if test -n "$arg_next"
then
	nextdate=$(date "+%F" --date="$arg_next") ||
		die "failed to understand '$arg_next' as a date"
else
	nextdate=$(date "+%F" --date="$today + 1 month")
	day_of_week=$(date "+%u" --date="$nextdate")
	test "$day_of_week" -lt 3 &&
		nextdate=$(date "+%F" --date="$nextdate + $(( 3 - $day_of_week )) days")
	test "$day_of_week" -gt 3 &&
		nextdate=$(date "+%F" --date="$nextdate + $(( 10 - $day_of_week )) days")
fi

echo "Next publication date is: $(date "+%A %B %d, %Y" --date="$nextdate")"


# Compute curdate, the publication date for the current edition

if test -n "$arg_cur"
then
	curdate=$(date "+%F" --date="$arg_cur") ||
		die "failed to understand '$arg_cur' as a date"
else
	curdate="$today"
fi

echo "Current publication date is: $(date "+%A %B %d, %Y" --date="$curdate")"


# Basic checks

type git >/dev/null || die "git not found" "we need git"

git show "$known_good_commit" >/dev/null 2>&1 ||
	die "$known_good_commit not found" \
	    "we need to be in a repo cloned from $repo_url"

cur_branch=$(git rev-parse --abbrev-ref HEAD)
test "$cur_branch" = "$main_branch" || die "please switch to the '$main_branch' branch"

test -d "$src_dir" || die "no source '$src_dir' directory"
test -d "$dst_dir" || die "no destination '$dst_dir' directory"

nb_ed=$(ls "$src_dir"/edition-*.md | wc -l)

test "$nb_ed" -eq 0 && die "no 'edition-*.md' file in '$src_dir' directory"
test "$nb_ed" -gt 1 && die "more than one 'edition-*.md' file in '$src_dir' directory"

type gh >/dev/null ||
	die "gh not found" "we need the GitHub CLI (gh) to create the issue"

gh auth status >/dev/null 2>&1 ||
    die "gh is not logged in" "please run 'gh auth login' or 'export GH_TOKEN=...'"


# Find edition info we need

edition=$(ls "$src_dir"/edition-*.md)

cur=$(expr "$edition" : "rev_news/drafts/edition-\([0-9]\+\).md")

test -n "$cur" || die "'$edition' should contain a number"

next=$(expr "$cur" + 1)


# Find dates we need

next_month_date=$(date "+%F" --date="$curdate + 15 days")
next_month=$(date "+%B %Y" --date="$next_month_date")
cur_year=$(date "+%Y" --date="$curdate")
next_year=$(date "+%Y" --date="$next_month_date")

# Set cur_month conditionally based on the year comparison
if [ "$cur_year" = "$next_year" ]; then
    # If the years are the same, just use the month name
    cur_month=$(date "+%B" --date="$curdate")
else
    # Otherwise, include the year for clarity
    cur_month=$(date "+%B %Y" --date="$curdate")
fi

f_day=$(date "+%-d" --date="$nextdate")
f_month=$(date "+%B" --date="$nextdate")
f_year=$(date "+%Y" --date="$nextdate")
full_date="$f_month $(add_order_suffix $f_day), $f_year"


# Publish current draft

git mv "$src_dir"/edition-$cur.md "$dst_dir"/$curdate-edition-$cur.markdown

git commit -m "Publish rn-$cur in $dst_dir/"


# Create a draft for next edition

next_ed="$src_dir/edition-$next.md"

edition_template="$basedir/templates/edition-XXX.md"
test -f "$edition_template" ||
	die "failed to find edition template at '$edition_template'"

cp "$edition_template" "$next_ed" ||
	die "failed to 'cp $edition_template $next_ed'"

next_ord=$(add_order_suffix "$next")

perl -pi -e "

	s/Edition _ED_NUM_/Edition $next/g;
	s/_ED_ORD_ edition/${next_ord} edition/g;
	s/_ED_DATE_/$nextdate/g;
	s/_ED_FULL_DATE_/$full_date/g;
	s/_ED_CUR_MONTH_YEAR_/$cur_month/g;
	s/_ED_NEXT_MONTH_YEAR_/$next_month/g;

" "$next_ed"

git add "$next_ed" ||
	die "failed to 'git add $next_ed'"

git commit -m "Add draft for rn-$next"

# Create GitHub Issue for the next edition

echo "Creating GitHub issue for edition $next..."

# Construct the issue body
issue_body="A currently mostly empty draft is there:

https://github.com/$repo_name/blob/master/rev_news/drafts/edition-$next.md

Feel free to comment in this issue, suggest topics, suggest persons to interview, or use the edit button (that looks like a pen) to edit and create a pull request with the changes you would like.

Let's try to publish this edition around the end of $f_month $f_year!

Thanks!

cc @jnareb @mjaix @sivaraam @gitster @stepnem"

# Create the issue
gh issue create \
    --repo "$repo_name" \
    --title "Any comment about upcoming Git Rev News edition $next" \
    --body "$issue_body"

echo "Issue created successfully. Remember to 'git push' so the draft link in the issue becomes valid!"
