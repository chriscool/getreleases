#!/bin/sh

releases="$1"

gitkraken_releases="https://support.gitkraken.com/release-notes/current"

die () {
	printf "%s\n" "$@"
	exit 1
}

if test -z "$releases"
then
	tmpdir=$(mktemp -d) || die "Failed to create temp dir using mktemp."

	releases="$tmpdir/gitkraken_releases.html"

	wget "$gitkraken_releases" -O "$releases" ||
		die "Failed to wget '$gitkraken_releases'."
fi

perl -ne '@a = m/id="version-([\d-]+)-[\S-]+">Version ([^<]+)</gsm;
     while (my ($u, $v) = splice (@a, 0, 2)) {
         print "[Git Kraken $v](https://support.gitkraken.com/release-notes/current#v$u)\n";
     }' "$releases"
