# Spotify Playlist Manager - Flow Diagrams

## Command Processing Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │     │             │
│  Parse      │────▶│  Validate   │────▶│  Execute    │────▶│  Display    │
│  Arguments  │     │  Command    │     │  Command    │     │  Results    │
│             │     │             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

## Song Import Process

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │     │             │
│  Read       │────▶│  Validate   │────▶│  Generate   │────▶│  Store in   │
│  Input File │     │  Songs      │     │  Embeddings │     │  Database   │
│             │     │             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
      │                   │
      │                   │
      ▼                   ▼
┌─────────────┐     ┌─────────────┐
│             │     │             │
│  Skip       │     │  Check      │
│  Comments   │     │  Spotify    │
│             │     │  Existence  │
└─────────────┘     └─────────────┘
                          │
                          │
                          ▼
                    ┌─────────────┐
                    │             │
                    │  Filter     │
                    │  Popular    │
                    │  Artists    │
                    └─────────────┘
```

## Playlist Update Process

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │     │             │
│  Select     │────▶│  Search     │────▶│  Delete     │────▶│  Create     │
│  Songs      │     │  Spotify    │     │  Existing   │     │  New        │
│             │     │  URIs       │     │  Playlist   │     │  Playlist   │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
      │                                                            │
      │                                                            │
      ▼                                                            ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │     │             │
│  Unused     │     │  Recently   │     │  Similar    │     │  Add        │
│  Songs      │     │  Unused     │     │  Songs      │     │  Tracks     │
│             │     │  Songs      │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
                                                                  │
                                                                  │
                                                                  ▼
                                                            ┌─────────────┐
                                                            │             │
                                                            │  Update     │
                                                            │  History    │
                                                            │             │
                                                            └─────────────┘
```

## Spotify Authentication Flow

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │     │             │
│  Check      │────▶│  Request    │────▶│  User       │────▶│  Store      │
│  Token      │     │  Auth       │     │  Authorizes │     │  Token      │
│  Cache      │     │             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
      │                                                            │
      │                                                            │
      ▼                                                            ▼
┌─────────────┐                                             ┌─────────────┐
│             │                                             │             │
│  Use        │                                             │  Refresh    │
│  Cached     │                                             │  When       │
│  Token      │                                             │  Expired    │
└─────────────┘                                             └─────────────┘
```

## Backup and Restore Process

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Create     │────▶│  Copy       │────▶│  Verify     │
│  Backup Dir │     │  Data Files │     │  Backup     │
│             │     │             │     │             │
└─────────────┘     └─────────────┘     └─────────────┘


┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │     │             │
│  Validate   │────▶│  Rename     │────▶│  Copy       │────▶│  Verify     │
│  Backup     │     │  Current    │     │  Backup     │     │  Restore    │
│             │     │  Data       │     │  Files      │     │             │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

## Database Cleaning Process

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│             │     │             │     │             │
│  Load All   │────▶│  Check Each │────▶│  Remove     │
│  Songs      │     │  Song       │     │  Invalid    │
│             │     │             │     │  Songs      │
└─────────────┘     └─────────────┘     └─────────────┘
                          │
                          │
                          ▼
                    ┌─────────────┐     ┌─────────────┐
                    │             │     │             │
                    │  Verify     │────▶│  Check      │
                    │  Spotify    │     │  Artist     │
                    │  URI        │     │  Popularity │
                    └─────────────┘     └─────────────┘
```
