#!/bin/sh

# Get announces from the Git Mailing list from the public inbox repo
# that contains all the messages from the mailing list.

since="$1"

test -n "$since" || since="45.days.ago"

ml_dir="$HOME/git/mailing-list/git.git"

die () {
	printf "%s\n" "$@"
	exit 1
}

cd "$ml_dir" || die "Failed to cd into '$ml_dir'."

tmpdir=$(mktemp -d) || die "Failed to create temp dir using mktemp."

git log --oneline --since="$since" > "$tmpdir/msgs.txt" ||
	die "Failed to get messages since '$since'."

grep -i announce "$tmpdir/msgs.txt" > "$tmpdir/announces.txt" ||
	die "No announces found since '$since'."

while read -r sha1 title
do
	git show "$sha1" > "$tmpdir/cur_msg.txt" ||
		die "Failed to show message for '$sha1'."
	perl -ne 'print "$1\n" if m/^\+Message-ID: <(.*)>$/i;' "$tmpdir/cur_msg.txt" > "$tmpdir/msg_id.txt" ||
		die "Failed to get message ID for '$sha1'."
	msg_id=$(head -1 "$tmpdir/msg_id.txt") ||
		die "Failed to get first message ID for '$sha1'."
	test -n "$msg_id" ||
		die "Failed to get non empty message ID for '$sha1'."
	msg_link="https://public-inbox.org/git/$msg_id"

	echo "[$title]($msg_link)"

done < "$tmpdir/announces.txt"
