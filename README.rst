ElectrumSVP
===========

ElectrumSVP is a lightweight Bitcoin SV (BSV) desktop wallet based on the ElectrumSV codebase.

ElectrumSVP is developed and maintained independently as a continuation of the ElectrumSV desktop wallet project, with a focus on reliable SPV verification, wallet security, fee handling, and practical BSV wallet functionality.

Current Release
---------------

**ElectrumSVP 0.2.0**

This is the current official release of ElectrumSVP. Users should report bugs, unexpected behavior, and compatibility issues encountered while using the software.

Download the latest release from the official GitHub releases page:

https://github.com/TruthMachine/ElectrumSVP/releases

Project website:

https://electrumsv.io/

Release Verification
--------------------

ElectrumSVP release artifacts are published with SHA-256 checksums and PGP signatures.

For ElectrumSVP 0.2.0, the release signing key fingerprint is::

    E9BA FA19 B15E 6C2C 158C DD40 0DB1 6C74 A9CA 7CF6

The release assets include:

* SHA-256 checksums
* PGP signatures for the release checksums
* PGP signatures for individual release binaries

Users who verify downloads should obtain the signing key independently and confirm that its fingerprint matches the fingerprint published by the project before trusting a signature.

Features
--------

ElectrumSVP currently includes:

* BIP39 seed support
* Electrum seed restoration
* Private-key sweeping
* Pay-to-many transactions
* Coin freezing and coin control
* Multiple wallet accounts
* Multisignature wallet support
* SPV transaction and UTXO verification
* BREAD verification and export
* Blockchain header verification and recovery
* Local proof-data handling
* Fee estimation and fee controls
* USD/fiat conversion
* Address and destination management
* Light, Dark, Pink, and Orange themes
* Windows, macOS, and Linux builds

Supported Platforms
-------------------

Official release builds are provided for:

* Windows
* macOS
* Linux

Release binaries and their corresponding signatures and checksums are available from the GitHub release page.

Running from Source
-------------------

ElectrumSVP is primarily a Python application.

For developers who want to run the application directly from source, clone the repository::

    git clone https://github.com/TruthMachine/ElectrumSVP.git
    cd ElectrumSVP

Install the project's dependencies appropriate for your platform, then run::

    python3 electrum-sv

The exact Python and dependency requirements may change as development continues. For the most reliable experience, users should generally use the official packaged releases rather than running directly from source.

Reporting Bugs
--------------

Bug reports, compatibility problems, and other technical issues are welcome.

Please report issues through the project's GitHub issue tracker:

https://github.com/TruthMachine/ElectrumSVP/issues

When reporting a problem, include:

* ElectrumSVP version
* Operating system and version
* Relevant error messages or logs
* Steps needed to reproduce the problem
* Any other information that may help diagnose the issue

Please do not include seed phrases, private keys, passwords, or other sensitive wallet information in an issue.

Development
-----------

ElectrumSVP is an independent continuation of the ElectrumSV codebase.

Development takes place in the GitHub repository:

https://github.com/TruthMachine/ElectrumSVP

Pull requests and technical contributions are welcome.

License
-------

ElectrumSVP retains the applicable licensing and copyright notices from the ElectrumSV codebase.

See ``LICENCE`` in this repository for the applicable license text and notices.

Website
-------

https://electrumsv.io/
