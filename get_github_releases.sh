#!/bin/sh

releases="$1"

github_releases="https://enterprise.github.com/releases.rss"

die () {
	printf "%s\n" "$@"
	exit 1
}

if test -z "$releases"
then
	tmpdir=$(mktemp -d) || die "Failed to create temp dir using mktemp."

	releases="$tmpdir/github_releases.rss"

	wget "$github_releases" -O "$releases" ||
		die "Failed to wget '$github_releases'."
fi

perl -ne '@a = m/<(title|link)>([^<]+)</gsm;
     for $b (@a) {
     	if ($b eq "title") {
	   $n = "t";
	} elsif ($b eq "link") {  
     	   $n = "l";
	} elsif ($n eq "t") {
	   $t = $b;
           $n = "";
	} elsif ($n eq "l") {  
	   $l = $b;
           $n = "";
	   print "[$t]($l)\n";
        }
     } ' "$releases"
