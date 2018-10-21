#!/bin/sh

releases="$1"

gerrit_releases="https://gerrit-releases.storage.googleapis.com/"

die () {
	printf "%s\n" "$@"
	exit 1
}

if test -z "$releases"
then
	tmpdir=$(mktemp -d) || die "Failed to create temp dir using mktemp."

	releases="$tmpdir/gerrit_releases.html"

	wget "$gerrit_releases" -O "$releases" ||
		die "Failed to wget '$gerrit_releases'."
fi

perl -ne '@a = m/<Key>gerrit-((\d+)\.(\d+)(\.\d+)?)\.war<\/Key><Generation>\d+<\/Generation><MetaGeneration>\d+<\/MetaGeneration><LastModified>([^<]*)<\/LastModified>/gsm;
     print "Gerrit ";
     my %times;
     while (my ($h, $u, $v, $w, $t) = splice (@a, 0, 5)) {
         $w = substr $w, 1;
         $times{$t} = [$h, $u, $v, $w];
     }
     my $count = 20;
     for my $t (reverse sort keys %times) {
         my ($h, $u, $v, $w) = @{$times{$t}};
         #print "h: $h, u: $u, v: $v, w: $w, t: $t\n";
         my $l = "";
         $l = "#$u$v$w" if ($w > 0);
         print "[$h](https://www.gerritcodereview.com/$u.$v.html$l)\n";
         $count--;
         last unless ($count > 0);
     }' "$releases"

