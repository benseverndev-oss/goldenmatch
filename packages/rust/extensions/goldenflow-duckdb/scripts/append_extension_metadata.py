import argparse
import shutil

def start_signature():
    # This is needed so that Wasm binaries are valid
    encoded_string = ''.encode('ascii')
    # 0 for custom section
    encoded_string += int(0).to_bytes(1, byteorder='big')
    # 213 in hex = 531 in decimal, total lenght of what follows (1 + 16 + 2 + 8x32 + 256)
    # [1(continuation) + 0010011(payload) = \x93 -> 147, 0(continuation) + 10(payload) = \x04 -> 4]
    encoded_string += int(147).to_bytes(1, byteorder='big')
    encoded_string += int(4).to_bytes(1, byteorder='big')
    # 10 in hex = 16 in decimal, lenght of name, 1 byte
    encoded_string += int(16).to_bytes(1, byteorder='big')
    # the name of the WebAssembly custom section, 16 bytes
    encoded_string += b'duckdb_signature'
    # 1000 in hex, 512 in decimal
    # [1(continuation) + 0000000(payload) = -> 128, 0(continuation) + 100(payload) -> 4],
    encoded_string += int(128).to_bytes(1, byteorder='big')
    encoded_string += int(4).to_bytes(1, byteorder='big')
    return encoded_string

def padded_byte_string(input):
    encoded_string = input.encode('ascii')
    encoded_string += b'\x00' * (32 - len(encoded_string))
    return encoded_string

def main():
    arg_parser = argparse.ArgumentParser(description='Append extension metadata to loadable DuckDB extensions')

    arg_parser.add_argument('-l','--library-file', type=str, help='Path to the raw shared library', required=True)
    arg_parser.add_argument('-n', '--extension-name', type=str, help='Extension name to use', required=True)

    arg_parser.add_argument('-o', '--out-file', type=str, help='Explicit path for the output file', default='')

    # The platform
    arg_parser.add_argument('-p', '--duckdb-platform', type=str, help='The DuckDB platform to encode')
    arg_parser.add_argument('-pf', '--duckdb-platform-file', type=str, help='The file containing the DuckDB platform to encode')

    arg_parser.add_argument('-dv', '--duckdb-version', type=str, help='The DuckDB version to encode, depending on the ABI type '
                                                               'this encodes the duckdb version or the C API version', required=True)
    arg_parser.add_argument('-ev', '--extension-version', type=str, help='The Extension version to encode')
    arg_parser.add_argument('-evf', '--extension-version-file', type=str, help='The file containing the Extension version to encode')

    arg_parser.add_argument('--abi-type', type=str, help='The ABI type to encode, set to C_STRUCT by default', default='C_STRUCT')

    args = arg_parser.parse_args()

    OUTPUT_FILE = args.out_file if args.out_file else args.extension_name + '.duckdb_extension'
    OUTPUT_FILE_TMP = OUTPUT_FILE + ".tmp"

    print("Creating extension binary:")

    # Start with copying the library to a tmp file
    print(f" - Input file: {args.library_file}")
    print(f" - Output file: {OUTPUT_FILE}")
    shutil.copyfile(args.library_file, OUTPUT_FILE_TMP)

    # Handle the platform
    PLATFORM = ""
    if args.duckdb_platform:
        PLATFORM = args.duckdb_platform
    elif args.duckdb_platform_file:
        try:
            with open(args.duckdb_platform_file, 'r') as file:
                PLATFORM = file.read().strip()
        except Exception as e:
            print(f"Failed to read platform from file {args.duckdb_platform_file}")
            raise
        if not PLATFORM:
            raise Exception(f"Platform file passed to script is empty: {args.duckdb_platform_file}")
    else:
        raise Exception(f"Neither --duckdb-platform nor --duckdb-platform-file found, please specify the platform using either")

    EXTENSION_VERSION = ""
    if args.extension_version:
        EXTENSION_VERSION = args.extension_version
    elif args.extension_version_file:
        try:
            with open(args.extension_version_file, 'r') as file:
                EXTENSION_VERSION = file.read().strip()
        except Exception as e:
            print(f"Failed to read extension version from file {args.extension_version_file}")
            raise
        if not EXTENSION_VERSION:
            raise Exception(f"Extension version file passed to script is empty: {args.extension_version_file}")
    else:
        raise Exception(f"Neither --extension-version nor --extension-version-file found, please specify the extension version using either")



    # Then append the metadata to the tmp file
    print(f" - Metadata:")
    with open(OUTPUT_FILE_TMP, 'ab') as file:
        file.write(start_signature())
        print(f"   - FIELD8 (unused)            = EMPTY")
        file.write(padded_byte_string(""))
        print(f"   - FIELD7 (unused)            = EMPTY")
        file.write(padded_byte_string(""))
        print(f"   - FIELD6 (unused)            = EMPTY")
        file.write(padded_byte_string(""))
        print(f"   - FIELD5 (abi_type)          = {args.abi_type}")
        file.write(padded_byte_string(args.abi_type))
        print(f"   - FIELD4 (extension_version) = {EXTENSION_VERSION}")
        file.write(padded_byte_string(EXTENSION_VERSION))
        print(f"   - FIELD3 (duckdb_version)    = {args.duckdb_version}")
        file.write(padded_byte_string(args.duckdb_version))
        print(f"   - FIELD2 (duckdb_platform)   = {PLATFORM}")
        file.write(padded_byte_string(PLATFORM))
        print(f"   - FIELD1 (header signature)  = 4 (special value to identify a duckdb extension)")
        file.write(padded_byte_string("4"))

        # Write some empty space for the signature
        file.write(b"\x00" * 256)


    # Finally we mv the tmp file to complete the process
    shutil.move(OUTPUT_FILE_TMP, OUTPUT_FILE)

if __name__ == '__main__':
    main()