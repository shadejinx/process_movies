#!/usr/bin/python

from __future__ import division
import PTN
import argparse
import requests
import json
import os
import sys
import logging
import shutil
import libffprobe
import libplexdb
from fuzzywuzzy import fuzz

library_dir = '/mnt/movies'
staging_dir = '/mnt/staging'

plexdb = '/var/lib/plexmediaserver/Library/Application Support/Plex Media Server/Plug-in Support/Databases/com.plexapp.plugins.library.db'
plex_library_name = 'Movies'

log_file = '/var/log/aria2/process_file.log'

mvdb_apikey = 'MVDB_API_KEY'

ffprobe_path = '/usr/bin/ffprobe'

######

def mungeCodec( inCodec ):
### mungeCodec
#       Input: inCodec (string)
#       Output: codec (string)
#               Errors to None

        codec = str(inCodec) if isinstance( inCodec, basestring ) else None

        if codec:
                if 'mpeg2' in codec or 'mpeg-2' in codec:
                        codec = 'mpeg2'
                elif 'hev' in codec or 'h265' in codec:
                        codec = 'h265'
                elif 'avc' in codec or 'h264' in codec:
                        codec = 'h264'
                elif codec in [ 'dx50', 'xvid', 'div3', 'divx' ] or 'mpeg-4' in codec or 'mpeg4' in codec:
                        codec = 'mpeg4'
                else:
                        codec = 'unknown'

        codec = str(codec) if codec else None

        return codec


def getMVDBResult( inTitle, inYear ):
### getMVDBResult
#       Input: Title (string), Year (int)
#       Output: JSON blob
#               Errors to None

        title = str(inTitle) if inTitle else ''
        year = int(inYear) if inYear else 0

        url = "https://api.themoviedb.org/3/search/movie"
        isJSON = False

        payload = {'api_key' : mvdb_apikey, 'query' : inTitle, 'year' : inYear }
        response = requests.request("GET", url, data=payload)

        if response.status_code == requests.codes.ok:
                res_json = response.json()

        	try:
                	json.dumps(res_json)
                	isJSON = True
        	except ValueError:
                	isJSON = False

        if isJSON and res_json and res_json['results']:
                return( res_json['results'] )
        else:
                return( None )


def calcVideoScore( inCodec, inBitrate, inPixels, inFramerate ):
### calcVideoScore
#       Input: codec (string), bitrate (int), pixels (int), framerite (float)
#       Outout: score (int)
#               Errors to 0

        codec = str(inCodec) if inCodec else ''
        bitrate = int(inBitrate) if inBitrate else 0
        pixels = int(inPixels) if inPixels else 0
        framerate = float(inFramerate) if inFramerate else 0

        score = 0

        if pixels and framerate:
                bpp = bitrate / ( pixels * framerate )
        else:
                bpp = 0

        for i in [ .05, .08, 0.1, 0.2, 1 ]:
                if bpp > i:
                        continue
                else:
                        score = [ .05, .08, 0.1, 0.2, 1 ].index(i)
                        break

        score += 1 if codec == 'h265' else 0

        score = int(score) if score else 0

        return score


def calcAudioScore( inCodec, inBitrate, inChannels, inLanguage, inSubtitles ):
### calcAudioScore
#       Input : inCodec (string), inBitrate (int), inChannels (int), inLanguage (string), inSubtitles (BOOL)
#       Output: score (int)
#               Errors to 0

        codec = str(inCodec) if inCodec else ''
        bitrate = int(inBitrate) if inBitrate else 0
        channels = int(inChannels) if inChannels else 0
        language = str(inLanguage) if inLanguage else 'unknwon'
        subtitles = True if inSubtitles else False

        score = 0

        score += 2 if channels >= 6 else 0

        if ( not language == 'english' and subtitles ) or language == 'english':
                score += 2

        for i in [ 98000, 127000, 150000, 256000, 100000000 ]:
                if bitrate > i:
                        continue
                else:
                        score += [ 98000, 127000, 150000, 256000, 100000000 ].index(i)
                        break

        score += 1 if codec in [ 'ac3', 'eac3', 'dca' ] else 0

        score = int(score) if score else 0

        return score


def calcTotalScore( inVideoScore, inAudioScore, inYear, inHighDef ):
### caclTotalScore
#       Input : inVideoScore (int), inAudioScore (int), inYear (int), inHighDef (BOOL)
#       Output: total_score (float)
#               Errors to None

        vid_score = int(inVideoScore) if inVideoScore else 0
        aud_score = int(inAudioScore) if inAudioScore else 0
        year = int(inYear) if inYear else 0
        high_def = True if inHighDef else False

        score = 0

        if year < 1977:
                # Be more lenient on classic movies
                score = vid_score * 1.2 + aud_score * 1.5
        elif high_def:
                # Be more stringent on genres that generally require a higher quality encode
                score = vid_score * 0.9 + aud_score * 0.75
        else:
                score = vid_score + aud_score * 0.9

        score = float(score) if score else 0

        return score


### CONFIGURE LOGGING
log = logging.getLogger('process_files.py')
log_hdlr = logging.FileHandler(log_file)
log_fmt = logging.Formatter('%(asctime)s [%(process)d] %(levelname)s: %(message)s')
log_hdlr.setFormatter(log_fmt)
log.addHandler(log_hdlr)
log.setLevel(logging.INFO)


### CONFIGURE ARGUMENT PARSING
aparse = argparse.ArgumentParser(description='Process movie files into Plex')
aparse.add_argument('-f', '--file', dest='file', required=True, help='a file to process')
aparse.add_argument('-d', '--dry-run', dest='dryrun', action='store_true', help='process files but do not move them')
aparse.add_argument('-v', '--verbose', dest='verbose', action='store_true', help='get more detail')
aparse.add_argument('-r', '--replace', dest='replace', action='store_true', help='replace files in your library if better exists')
aparse.add_argument('--mvdb-api-key', dest='mvdb_apikey', help='mvdb api key')

args = aparse.parse_args()

full_path = os.path.abspath(args.file)
verbose = args.verbose
replace = args.replace
dryrun = args.dryrun

if args.mvdb_apikey:
	mvdb_apikey = args.mvdb_apikey

if verbose:
        log.setLevel(logging.DEBUG)


### START PROCESSING FILE
if not os.path.exists(full_path):
        log.error('#### FINISH: File does not exist: ' + full_path)
        sys.exit(1)

log.info('#### START: Processing: ' + full_path )
if dryrun:
        log.info('Dry Run enabled, no file operations will be performed')
if replace:
        log.info('Replace enabled')

src_file = os.path.basename(full_path)


### GET FFPROBE INFORMATION FROM FILE
video = libffprobe.getFFProbeInfo( ffprobe_path, full_path, 'v' )
if video:
        codec, bitrate, ratio, pixels, framerate = libffprobe.getVideoInfo( video )
else:
        log.error('#### FINISH: Error reading: ' + full_path)
        sys.exit(1)

codec = '' if not codec else codec
bitrate = 0 if not bitrate else bitrate
if not bitrate:
        log.warn('Bitrate not found in metadata, calculating average bitrate.')
        bitrate = libffprobe.calcBitRate( ffprobe_path, full_path )

ratio = 0 if not ratio else ratio
pixels = 0 if not pixels else pixels
framerate = 0 if not framerate else framerate

audio = libffprobe.getFFProbeInfo( ffprobe_path, full_path, 'a' )
if audio:
        aud_codec, language, channels, aud_bitrate = libffprobe.getAudioInfo( audio )
else:
        log.error('#### FINISH: Error reading: ' + full_path)
        sys.exit(1)

aud_codec = '' if not aud_codec else aud_codec
language = '' if not language else language
channels = 0 if not channels else channels
aud_bitrate = 0 if not aud_bitrate else aud_bitrate

subtitles = libffprobe.getFFProbeInfo( ffprobe_path, full_path, 's' )

if subtitles:
        eng_subtitles = libffprobe.hasEngSubtitles( subtitles )
else:
        eng_subtitles = False

bpp = bitrate / ( pixels * framerate )


### PARSE FILE AND PATH INFORMATION FOR MOVIE TITLE AND DATE
file_info = PTN.parse(src_file)

if 'episode' in file_info:
        log.error('#### FINISH: TV show detected, skipping.')
        sys.exit(1)

if not 'title' in file_info or not 'year' in file_info:
        log.warn('Filename parsing failure for ' + src_file + ', fuzzy matching on path.')
        parent_dir = os.path.basename(os.path.dirname(full_path))

        file_info = PTN.parse(parent_dir)

        if not 'title' in file_info or not 'year' in file_info:
                log.error('#### FINISH: Failure parsing path ' + parent_dir + ', manual processing needed.')
                sys.exit(1)

title = file_info['title'].replace('.',' ').strip(",'!%/ ").title()
year = str(file_info['year'])


### SEARCH MVDB FOR INFORMATION
log.debug('Checking MVDB for: \'' + title + '\' in ' + year )
res = getMVDBResult( title, year )

if not res:
        log.warn('No results from MVDB for: \'' + title + '\' in ' + year)
        if '-' in title or ':' in title:
                split_title = title.replace('-', ':').strip().split(':')
                log.debug('Attempting munge title: \'' + split_title[0] + '\' in ' + year)
                res = getMVDBResult( split_title[0], year )

        if not res:
                log.error('#### FINISH: No results from MVDB, check ' + src_file + ' for naming errors.')
                sys.exit(1)

prev_score = None

for idx in range(len(res)):
        if year in res[idx]['release_date']:
                title_len = len(title)
                res_len = len(res[idx]['title'])
                threshold = 55 + (( 1 - ( abs(title_len - res_len) / max(title_len, res_len))) * 30 )
                score = fuzz.token_sort_ratio(title.lower(), res[idx]['title'].lower())
                if score >= threshold and score > prev_score:
                        prev_score = score
                        mvdb_title = res[idx]['title'].lower()
                        mvdb_date = res[idx]['release_date']
                        mvdb_language = res[idx]['original_language']
                        mvdb_genres = res[idx]['genre_ids']

if not prev_score:
        log.warn('A definitive match cannot be found in mVDB, munging title and searching again')
        if str(year) in res[0]['release_date'] or str(int(year) - 1) in res[0]['release_date'] or str(int(year) + 1) in \
	res[0]['release_date']:
                if ':' in res[0]['title'] or '-' in res[0]['title']:
                        split_mvdb = res[0]['title'].replace('-', ':').split(':')
                        munge_title = split_mvdb[0].lower()
                        threshold = 90
                else:
                        title_len = len(title)
                        res_len = len(res[0]['title'])
                        munge_title = res[0]['title'].lower()
                        threshold = 55 + (( 1 - ( abs(title_len - res_len) / max(title_len, res_len))) * 25 )

                score = fuzz.token_sort_ratio( title.lower(), munge_title )
                if score >= threshold:
                        prev_score = score
                        mvdb_title = res[0]['title'].lower()
                        mvdb_date = res[0]['release_date']
                        mvdb_language = res[0]['original_language']
                        mvdb_genres = res[0]['genre_ids']

if prev_score:
        log.debug('MVDB match at ' + str(prev_score) + '%: ' + title + ', ' + year + ' => ' \
		+  mvdb_title.title() + ', ' + mvdb_date )
        title = mvdb_title.strip(",'!%/").replace(":", " -").title()
else:
        log.error('#### FINISH: MVDB has results but a definitive match was not found, edit filename and try again' )
        sys.exit(1)


#MVDB Genres
# 12:Adventure, 14:Fantasy, 16:Animation, 27:Horror, 28:Action, 878:Science-Fiction

### If file is in one of the above genres, it wants for a higher quality file
high_def = False

for genre in [ 12, 14, 16, 27, 28, 878 ]:
        if genre in mvdb_genres:
                high_def = True
                break


### SEARCH PLEX DATABASE FOR FILE
codeclist = [ 'mpeg2', 'h265', 'h264', 'mpeg4' ]
dest_dir = title + ' (' + year + ')'
duplicate = False

plex_section_id = libplexdb.getPlexSectionID( plexdb, plex_library_name )

if plex_section_id:
        plex_media_id = libplexdb.getPlexMediaID( plexdb, title, year, plex_section_id )

        if plex_media_id:
                duplicate = True
                old_dir, old_file = libplexdb.getPlexFileInfo( plexdb, plex_media_id )

                old_dir = '' if not old_dir else old_dir
                old_file = '' if not old_file else old_file

                old_codec, old_bitrate, old_pixels, old_fps = libplexdb.getPlexVideoInfo( plexdb, plex_media_id )

                ### If the information isn't in the Plex library, get it from the file
                ### If the file is on remote storage, this could be slow
                if ( not old_codec or not old_bitrate or not old_pixels or not old_fps ) and old_file:
                        old_video = libffprobe.getFFProbeInfo( ffprobe_path, old_file, 'v' )
                        if old_video:
                                old_codec, old_bitrate, old_pixels, old_fps = libffprobe.getVideoInfo( old_audio )

                old_codec = '' if not old_codec else str(mungeCodec(old_codec))
                old_bitrate = 0 if not old_bitrate else old_bitrate
                old_pixels = 0 if not old_pixels else old_pixels
                old_fps = 0 if not old_fps else old_fps

                old_aud_codec, old_lang, old_channels, old_aud_bitrate = libplexdb.getPlexAudioInfo( plexdb, plex_media_id )

                ### If the information isn't in the Plex library, get it from the file
                ### If the file is on remote storage, this could be slow
                if ( not old_aud_codec or not old_lang or not old_channels or not old_aud_bitrate) and old_file:
                        old_audio = libffprobe.getFFProbeInfo( ffprobe_path, old_file, 'a' )
                        if old_audio:
                                old_aud_codec, old_lang, old_channels, old_aud_bitrate = libffprobe.getAudioInfo( old_audio )

                old_aud_codec = '' if not old_aud_codec else old_aud_codec
                old_lang = 'unknown' if not old_lang else old_lang
                old_channels = 0 if not old_channels else old_channels
                old_aud_bitrate = 0 if not old_aud_bitrate else old_aud_bitrate

                #####
                ### INSERT CODE TO EXTRACT SUBTITLES FROM PLEXDB
                old_eng_subtitles = None
                #####

                ### If the information isn't in the Plex library, get it from the file
                ### If the file is on remote storage, this could be slow
                if old_eng_subtitles == None and old_file:
                        old_subtitles = libffprobe.getFFProbeInfo( ffprobe_path, old_file, 's' )
                        if not old_subtitles:
                                old_eng_subtitles = libffprobe.hasEngSubtitles( old_subtitles )
                        else:
                                old_eng_subtitles = False

else:
        log.error('#### FINISH: Plex section does not exist: ' + plex_section_id)
        sys.exit(1)


### DISPOSITION THE FILE
remove = False
staging = False
error = 0

if ( int(year) >= 1977 and ratio < 1.34 ) or bitrate < ( pixels * framerate ) * 0.04:
        log.error('Movie does not meet bare minimum requirements.')
        remove = True
        error = 1

elif codec == 'unknown':
        log.error('Movie video codec unknown.')
        staging = True

elif duplicate and ( not old_pixels or not old_bitrate ):
        log.error('File found in the Plex library, but not analyzed yet. Analyze "' + title + '" in Plex and rerun this script.')
        error = 1

elif not duplicate:
        log.debug('Found in the Plex library: FALSE')

        ### Check video quality
        log.debug('Video Stats: ' + codec + ', ' + str(int( bitrate / 1000 )) + 'kbps, ' + str(int( pixels / 1000 )) + 'k pixels.' )
        log.debug('Bits-Per-Pixel (BPP): ' + str(round(bpp, 3)))

        log.debug('High-def genre: ' + str(high_def).upper() )

        vid_score = calcVideoScore( codec, bitrate, pixels, framerate )

        ### SCORE AUDIO
        log.debug('Audio Stats: ' + language + ', ' + str(channels) + ' channels, ' + str(int( aud_bitrate / 1000 )) + 'kbps' )


        aud_score = calcAudioScore( aud_codec, aud_bitrate, channels, language, eng_subtitles )

        if language == 'english':
                log.debug('English audio track: TRUE')
        else:
                log.debug('English audio track: FALSE')

        log.debug('English subtitles: ' + str(eng_subtitles).upper())

        total_score = calcTotalScore( vid_score, aud_score, year, high_def )

        log.debug('Total quality score: ' + str(total_score))

        if total_score <= 3:
                remove = True
        elif total_score <= 8:
                staging = True

else:
        log.warn('Found in Plex library: TRUE')
        log.debug('Duplicate found in ' + old_file)

        estimated_bitrate = ( ( pixels / old_pixels ) ** 0.75 ) * old_bitrate
        old_bpp = old_bitrate / ( old_pixels * old_fps )

        #### VIDEO COMPARISON
        log.debug('Video Stats, OLD: ' + old_codec + ', ' + str(int( old_bitrate / 1000 )) + 'kbps, ' + str(int( old_pixels / 1000 )) \
		  + 'k pixels, BPP: ' + str(round(old_bpp, 3)) )
        log.debug('Video Stats, NEW: ' + codec + ', ' + str(int( bitrate / 1000 )) + 'kbps, ' + str(int( pixels / 1000 )) \
		  + 'k pixels, BPP: ' + str(round(bpp, 3)) )

        log.debug('Target bitrate for the rule of 0.75 is: ' + str(int( estimated_bitrate / 1000 )) + 'kbps.' )

        old_vidscore = calcVideoScore( old_codec, old_bitrate, old_pixels, old_fps )
        vidscore = calcVideoScore( codec, bitrate, pixels, framerate )

        log.debug('High-def genre: ' + str(high_def).upper() )

        # If the codec is the same, then the bitrate must be 20% than the rule of 0.75
        if codec == old_codec and int(bitrate) >= ( estimated_bitrate * 1.2 ):
                log.debug('Movie codec is equal and bitrate is at least 20% better than the previous.')

        # If the codec is better, the bitrate must be at least 75% of the rule of 0.75
        elif codeclist.index(codec) < codeclist.index(old_codec) and int(bitrate) >= ( estimated_bitrate * 0.75 ):
                log.debug('Movie codec is better and bitrate is at least 75% of the previous.')

        #If the codec is worse, the bitrate must be at least 170% of the rule of 0.75
        elif codeclist.index(codec) - codeclist.index(old_codec) == 1 and int(bitrate) >= ( estimated_bitrate * 1.7 ):
                log.debug('Movie codec is older, but the bitrate is more than 170% of the previous.')
                staging = True

        else:
                log.warn('Movie codec is older than previous and/or it does not meet bitrate target.')
                remove = True

        #### AUDIO COMPARISON
        log.debug('Audio Stats, OLD: ' + old_lang + ', ' + old_aud_codec + ', ' + str(old_channels) + ' channels, ' \
		  + str(int( old_aud_bitrate / 1000 )) + 'kbps, Eng Subtitles = ' + str(old_eng_subtitles))
        log.debug('Audio Stats, NEW: ' + language + ', ' + aud_codec + ', ' + str(channels) + ' channels, ' \
		  + str(int( aud_bitrate / 1000 )) + 'kbps, Eng Subtitles = ' + str(eng_subtitles))

        if ( channels >= old_channels or channels >= 6 ) and int(aud_bitrate) > 150000:
                log.debug('Movie audio track quality meets or exceeds the previous.')
        elif channels == 0 or int(aud_bitrate) == 0:
                log.debug('Movie audio track quality unknown.')
                staging = True
        else:
                log.warn('Movie audio track quality does not meet the standard of the previous.')
                remove = True

        old_audscore = calcAudioScore( old_aud_codec, old_aud_bitrate, old_channels, old_lang, old_eng_subtitles )
        audscore = calcAudioScore( aud_codec, aud_bitrate, channels, language, eng_subtitles )

        old_totalscore = calcTotalScore( old_vidscore, old_audscore, year, high_def )
        totalscore = calcTotalScore( vidscore, audscore, year, high_def )

        log.debug('Total Quality Score, OLD: ' + str(round(old_totalscore, 3)))
        log.debug('Total Quality Score, NEW: ' + str(round(totalscore, 3)))

        if totalscore > old_totalscore and totalscore > 3 and remove == True:
                remove = False
                staging = True


if remove:
        log.error('#### FINISH: ' + src_file + ' does not meet standards, deleting it.')
        if not dryrun:
                os.remove(full_path)
        error = 1
elif staging:
        log.info('#### FINISH: Unable to disposition ' + src_file + ', moving to staging.')
        if not dryrun:
                if not os.path.isdir( staging_dir + '/' + dest_dir ):
                        os.mkdir( staging_dir + '/' + dest_dir )
                shutil.move( full_path, staging_dir + '/' + dest_dir + '/' + src_file )
elif duplicate and replace:
        log.warn('#### FINISH: Replacing old file in Plex library: ' + old_filename + '.' )
        if not dryrun:
                shutil.move( full_path, old_filename )
else:
        log.info('#### FINISH: Copying ' + src_file + ' to Plex library.')
        out_file, out_ext = os.path.splitext(src_file)
        out_file = title + ' (' + year + ')' + out_ext
        if not dryrun:
                if not os.path.isdir( library_dir + '/' + dest_dir ):
                        os.mkdir( library_dir + '/' + dest_dir )
                shutil.move( full_path, library_dir + '/' + dest_dir + '/' + out_file )

sys.exit(error)
