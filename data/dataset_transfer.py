"""Transfer matching image bundles with recoverable publication and cleanup."""
from dataset_files import IMAGE_EXTENSIONS, COMPANION_EXTENSIONS as SIDECAR_EXTENSIONS, group_dataset_files
from file_io import file_hash, atomic_write_json, publish_file

import json
import os
from pathlib import Path
import shutil
import tempfile

STAGE_PREFIX = '.vlcaptioner-transfer-'


def matches(content, keywords, excluded, search_mode, contains):
    content = content.lower()
    tags = {tag.strip() for tag in content.replace('\n', ',').split(',') if tag.strip()}
    tag_match = bool(keywords) and (all(word in tags for word in keywords) if search_mode == 'AND'
                                   else any(word in tags for word in keywords))
    return (tag_match or bool(contains and contains in content)) and not any(word in tags for word in excluded)


def read_transaction(stage):
    record = json.loads((stage / 'transaction.json').read_text(encoding='utf-8'))
    names, hashes = record['names'], record['hashes']
    if (record['action'] not in ('copy', 'move') or not isinstance(names, list) or not names
            or len(set(names)) != len(names) or not isinstance(hashes, dict)
            or not isinstance(record['source'], str) or not Path(record['source']).is_absolute()):
        raise ValueError(f'Invalid transfer journal: {stage}')
    for name in names:
        if (not isinstance(name, str) or name in ('', '.', '..') or Path(name).name != name
                or not isinstance(hashes.get(name), str) or len(hashes[name]) != 64):
            raise ValueError(f'Invalid transfer journal: {stage}')
    return record


def rollback_transfer(stage, record):
    """Before commit, every source still exists; retire only our published files."""
    destination = stage.parent
    for name in record['names']:
        staged, target = stage / 'files' / name, destination / name
        # Windows rename consumes the staged file. POSIX hard links can leave
        # both names if interrupted between link() and unlink(). A racing
        # destination with a different inode belongs to someone else.
        installed = not staged.exists() or (target.exists() and os.path.samefile(staged, target))
        if installed and target.exists():
            if target.is_symlink() or file_hash(target) != record['hashes'][name]:
                raise OSError(f'Cannot roll back changed destination: {target}')
            target.unlink()
    shutil.rmtree(stage)


def finish_transfer(stage, record):
    """Check the complete destination before removing any original source."""
    destination, source = stage.parent, Path(record['source'])
    if source.resolve() != source:
        raise OSError(f'Transfer source folder changed: {source}')
    for name in record['names']:
        target = destination / name
        if target.is_symlink() or not target.is_file() or file_hash(target) != record['hashes'][name]:
            raise OSError(f'Transfer destination is incomplete or changed: {target}')
        original = source / name
        if record['action'] == 'move' and original.exists():
            if original.is_symlink() or file_hash(original) != record['hashes'][name]:
                raise OSError(f'Transfer source changed; original was preserved: {original}')
    if record['action'] == 'move':
        for name in record['names']:
            (source / name).unlink(missing_ok=True)
    shutil.rmtree(stage)


def recover_transfers(source, destination, emit):
    """Resume interrupted sets even when their selected caption already moved."""
    for stage in sorted(destination.glob(STAGE_PREFIX + '*')):
        if stage.is_symlink() or not stage.is_dir():
            continue
        if not (stage / 'transaction.json').exists():
            # Publication starts only after a complete journal exists. Leave
            # unrecognized folders alone; they may belong to the user.
            continue
        record = read_transaction(stage)
        if Path(record['source']) != source:
            continue
        if (stage / 'committed').exists():
            finish_transfer(stage, record)
        else:
            rollback_transfer(stage, record)
        emit(('log', f'Recovered interrupted {record["action"]} ({len(record["names"])} files).'))


def transfer_bundle(bundle, destination, action, caption):
    """Stage the full set, publish without overwriting, then clean up sources."""
    source = caption.parent.resolve()
    if action not in ('copy', 'move') or source == destination.resolve():
        raise ValueError('Choose copy or move and a different destination folder.')
    if any(path.is_symlink() or path.parent.resolve() != source for path in bundle):
        raise ValueError('Transfer sources must be regular files in the selected folder.')
    # Keep the selected caption until last during source cleanup. The journal
    # also makes the set discoverable after interruption at any cleanup point.
    bundle = sorted(bundle, key=lambda path: path == caption)
    stage = Path(tempfile.mkdtemp(dir=destination, prefix=STAGE_PREFIX))
    record = None
    try:
        payload = stage / 'files'
        payload.mkdir()
        hashes = {}
        for path in bundle:
            shutil.copy2(path, payload / path.name)
            hashes[path.name] = file_hash(payload / path.name)
            if file_hash(path) != hashes[path.name]:
                raise OSError(f'Source changed while copying: {path}')
        record = {'source': str(source), 'action': action,
                  'names': [path.name for path in bundle], 'hashes': hashes}
        atomic_write_json(stage / 'transaction.json', record)
        for path in bundle:
            staged, target = payload / path.name, destination / path.name
            publish_file(staged, target, overwrite=False)
        (stage / 'committed').touch()
    except Exception:
        if (stage / 'transaction.json').exists():
            rollback_transfer(stage, record)
        else:
            shutil.rmtree(stage)
        raise
    # Failures here keep the committed journal and complete destination for
    # recovery, including when deletion of one original fails midway through.
    finish_transfer(stage, record)


def transfer(settings, cancel, emit):
    """Perform filesystem work only; report results to the UI through its queue."""
    scanned = matched = transferred = skipped = errors = 0
    try:
        source, destination = settings['source'].resolve(), settings['destination'].resolve()
        if source == destination:
            raise ValueError('Source and destination must be different folders.')
        recover_transfers(source, destination, emit)
        files = sorted((path for path in source.iterdir() if path.is_file()), key=lambda path: path.name.lower())
        candidates = [path for path in files if path.suffix.lower() == settings['extension']]
        groups = group_dataset_files(files, extra_extensions=(settings['extension'],))
        group_keys = {path: key for key, paths in groups.items() for path in paths}
        emit(('progress', 0, len(candidates), 'Scanning caption files…'))
        for caption in candidates:
            if cancel.is_set():
                break
            if not caption.is_file():
                continue  # A previous matching bundle already moved this file.
            scanned += 1
            try:
                content = caption.read_text(encoding='utf-8-sig')
                if matches(content, settings['keywords'], settings['excluded'], settings['mode'], settings['contains']):
                    matched += 1
                    bundle = groups[group_keys[caption]]
                    conflicts = [path.name for path in bundle if (destination / path.name).exists()
                                 or (destination / path.name).is_symlink()]
                    if conflicts:
                        skipped += 1
                        emit(('log', f'Skipped {caption.stem}: destination already contains {", ".join(conflicts)}'))
                    else:
                        transfer_bundle(bundle, destination, settings['action'], caption)
                        transferred += len(bundle)
                        emit(('log', f'{"Copied" if settings["action"] == "copy" else "Moved"}: {caption.stem} ({len(bundle)} files)'))
            except Exception as error:
                errors += 1
                emit(('log', f'Error for {caption.name}: {error}. Continuing with the next caption.'))
            emit(('progress', scanned, len(candidates), caption.name))
    except Exception as error:
        errors += 1
        emit(('log', f'Transfer could not continue: {error}'))
    finally:
        emit(('done', cancel.is_set(), scanned, matched, transferred, skipped, errors))
