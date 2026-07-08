// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title AriaLedger — ancrage inviolable du track-record d'ARIA sur Base.
/// @notice On publie périodiquement la RACINE de Merkle de l'ensemble des verdicts ARIA.
///         Chaque ancrage est horodaté par le bloc : impossible de backdater ou réécrire un
///         verdict passé sans casser sa preuve d'appartenance (vérifiée hors chaîne).
/// @dev Volontairement minimal et sans transfert de valeur : la seule action est de stocker
///      une empreinte 32 octets. Seul l'`owner` peut ancrer. Aucun fonds ne transite ici.
contract AriaLedger {
    struct Anchor {
        bytes32 root;
        uint64 timestamp;
    }

    address public owner;
    Anchor[] private _anchors;

    /// @notice Émis à chaque ancrage d'une racine de track-record.
    event Anchored(uint256 indexed index, bytes32 indexed root, uint64 timestamp);
    event OwnershipTransferred(address indexed from, address indexed to);

    error NotOwner();
    error ZeroRoot();
    error ZeroAddress();

    modifier onlyOwner() {
        if (msg.sender != owner) revert NotOwner();
        _;
    }

    constructor(address initialOwner) {
        if (initialOwner == address(0)) revert ZeroAddress();
        owner = initialOwner;
        emit OwnershipTransferred(address(0), initialOwner);
    }

    /// @notice Ancre une nouvelle racine de Merkle. Retourne son index.
    function anchor(bytes32 root) external onlyOwner returns (uint256 index) {
        if (root == bytes32(0)) revert ZeroRoot();
        index = _anchors.length;
        _anchors.push(Anchor({root: root, timestamp: uint64(block.timestamp)}));
        emit Anchored(index, root, uint64(block.timestamp));
    }

    function anchorCount() external view returns (uint256) {
        return _anchors.length;
    }

    function anchorAt(uint256 index) external view returns (bytes32 root, uint64 timestamp) {
        Anchor storage a = _anchors[index];
        return (a.root, a.timestamp);
    }

    function latest() external view returns (bytes32 root, uint64 timestamp) {
        Anchor storage a = _anchors[_anchors.length - 1];
        return (a.root, a.timestamp);
    }

    function transferOwnership(address newOwner) external onlyOwner {
        if (newOwner == address(0)) revert ZeroAddress();
        emit OwnershipTransferred(owner, newOwner);
        owner = newOwner;
    }
}
