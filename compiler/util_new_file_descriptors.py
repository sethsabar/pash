from util import log
from definitions.ir.file_id import FileId
from definitions.ir.resource import FileResource, Resource, FileDescriptorResource
from definitions.ir.arg import Arg
# imports from annotation framework
import sys
from config import get_path_annotation_repo
sys.path.insert(1, get_path_annotation_repo())
# for typing
# --
# for use
from datatypes_new.BasicDatatypesWithIO import FileNameWithIOInfo, StdDescriptorWithIOInfo


def resource_from_file_descriptor(file_descriptor) -> Resource:
    if isinstance(file_descriptor, FileNameWithIOInfo):
        arg = file_descriptor.get_name()
        log(f'filedes name: {file_descriptor.get_name()}')
        log(f'filedes name type: {type(file_descriptor.get_name())}')
        log(f'arg: {arg}')
        return FileResource(file_descriptor.get_name())
    elif isinstance(file_descriptor, StdDescriptorWithIOInfo):
        resource = ("fd", file_descriptor.get_type().value)
        return FileDescriptorResource(resource)
    else:
        assert(False)
        # unreachable
